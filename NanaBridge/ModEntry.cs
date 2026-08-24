using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Characters;
using StardewValley.Monsters;
using StardewValley.TerrainFeatures;
using xTile.Dimensions;

namespace NanaBridge;

public sealed class ModEntry : Mod
{
    private const int SchemaVersion = 2;
    private const int DefaultLightExportIntervalTicks = 12;
    private const int DefaultHeavyExportIntervalTicks = 300;
    private const int PerfLogIntervalTicks = 600;
    private const int StaleTempCleanupSeconds = 30;
    private const int DefaultLightHeartbeatMs = 300;
    private const int LightPixelBucket = 2;
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = false,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private string dataDir = "";
    private string snapshotPath = "";
    private string lightSnapshotPath = "";
    private uint lastLightExportTick;
    private uint lastHeavyExportTick;
    private uint lastPerfLogTick;
    private uint exportSeq;
    private string? lastHeavyLocation;
    private Dictionary<string, object?>? lastLightExportMetrics;
    private Dictionary<string, object?>? lastHeavyExportMetrics;
    private readonly object lightWriterLock = new();
    private LightWriteRequest? pendingLightWrite;
    private bool lightWriterRunning;
    private string? lastLightSignature;
    private long lastLightWriteCompletedUnixMs;
    private uint lastLightWriteCompletedTick;
    private uint lastLightQueuedTick;

    public override void Entry(IModHelper helper)
    {
        this.dataDir = Path.Combine(helper.DirectoryPath, "data");
        this.snapshotPath = Path.Combine(this.dataDir, "state.json");
        this.lightSnapshotPath = Path.Combine(this.dataDir, "state_light.json");
        Directory.CreateDirectory(this.dataDir);
        this.CleanupStaleTempFiles();

        helper.Events.GameLoop.SaveLoaded += this.OnSaveLoaded;
        helper.Events.GameLoop.ReturnedToTitle += this.OnReturnedToTitle;
        helper.Events.GameLoop.UpdateTicked += this.OnUpdateTicked;

        this.ExportLightSnapshot("entry");
        this.ExportHeavySnapshot("entry");
        this.Monitor.Log($"NanaBridge ready; exporting read-only light state to {this.lightSnapshotPath}; heavy map snapshot to {this.snapshotPath}", LogLevel.Info);
    }

    private void OnSaveLoaded(object? sender, SaveLoadedEventArgs e)
    {
        this.ExportLightSnapshot("save_loaded");
        this.ExportHeavySnapshot("save_loaded");
    }

    private void OnReturnedToTitle(object? sender, ReturnedToTitleEventArgs e)
    {
        this.ExportLightSnapshot("returned_to_title");
        this.ExportHeavySnapshot("returned_to_title");
    }

    private void OnUpdateTicked(object? sender, UpdateTickedEventArgs e)
    {
        if (!Context.IsWorldReady)
        {
            if (e.Ticks % 120 == 0)
            {
                this.ExportLightSnapshot("world_not_ready");
                this.ExportHeavySnapshot("world_not_ready");
            }
            return;
        }

        string currentLocation = Game1.currentLocation?.NameOrUniqueName ?? "";
        bool mapChanged = !string.Equals(currentLocation, this.lastHeavyLocation, StringComparison.Ordinal);

        int lightExportTicks = this.ConfigInt("NANA_BRIDGE_LIGHT_EXPORT_TICKS", DefaultLightExportIntervalTicks, 1, 120);
        int heavyExportTicks = this.ConfigInt("NANA_BRIDGE_HEAVY_EXPORT_TICKS", DefaultHeavyExportIntervalTicks, 150, 3600);

        bool lightWriteQueued = false;
        if (e.Ticks - this.lastLightExportTick >= lightExportTicks)
        {
            lightWriteQueued = this.ExportLightSnapshot("tick");
            this.lastLightExportTick = e.Ticks;
        }

        bool heavyIntervalDue = e.Ticks - this.lastHeavyExportTick >= heavyExportTicks;
        if (!mapChanged && heavyIntervalDue && lightWriteQueued)
        {
            this.RecordHeavyDeferredMetric(e.Ticks);
        }
        else if (mapChanged || heavyIntervalDue)
        {
            this.ExportHeavySnapshot(mapChanged ? "map_changed" : "heavy_interval");
            this.lastHeavyExportTick = e.Ticks;
            this.lastHeavyLocation = currentLocation;
        }
    }

    private bool ExportLightSnapshot(string reason)
    {
        try
        {
            uint seq = ++this.exportSeq;
            var totalWatch = Stopwatch.StartNew();
            var buildWatch = Stopwatch.StartNew();
            var snapshot = this.BuildLightSnapshot(reason);
            buildWatch.Stop();

            snapshot["export_seq"] = seq;
            snapshot["exportSeq"] = seq;
            snapshot["exportTick"] = Game1.ticks;
            snapshot["exportReason"] = reason;
            snapshot["exportKind"] = "light";
            snapshot["light_export_ticks"] = this.ConfigInt("NANA_BRIDGE_LIGHT_EXPORT_TICKS", DefaultLightExportIntervalTicks, 1, 120);
            snapshot["heavy_export_ticks"] = this.ConfigInt("NANA_BRIDGE_HEAVY_EXPORT_TICKS", DefaultHeavyExportIntervalTicks, 150, 3600);

            string signature = this.BuildLightSignature(snapshot);
            uint currentTick = (uint)Math.Max(0, Game1.ticks);
            int heartbeatMs = this.ConfigInt("NANA_BRIDGE_LIGHT_HEARTBEAT_MS", DefaultLightHeartbeatMs, 100, 2000);
            long? lastLightWriteAgeMs = this.LastLightWriteAgeMs();
            bool heartbeatDue = lastLightWriteAgeMs == null || lastLightWriteAgeMs.Value >= heartbeatMs;
            bool unchanged = string.Equals(signature, this.lastLightSignature, StringComparison.Ordinal);
            if (unchanged && !heartbeatDue)
            {
                totalWatch.Stop();
                this.lastLightExportMetrics = this.LightSkippedMetrics(reason, seq, currentTick, heartbeatDue, heartbeatMs, buildWatch.Elapsed.TotalMilliseconds, totalWatch.Elapsed.TotalMilliseconds);
                return false;
            }

            string writeReason = heartbeatDue && unchanged ? "heartbeat_unchanged" : "changed";
            if (writeReason == "heartbeat_unchanged")
            {
                reason = "heartbeat_unchanged";
                snapshot["exportReason"] = reason;
            }

            snapshot["exportMetrics"] = new Dictionary<string, object?>
            {
                ["export_seq"] = seq,
                ["export_tick"] = Game1.ticks,
                ["export_kind"] = "light",
                ["export_reason"] = reason,
                ["build_snapshot_ms"] = Math.Round(buildWatch.Elapsed.TotalMilliseconds, 3),
                ["light_export_skipped"] = false,
                ["heartbeat_due"] = heartbeatDue,
                ["light_heartbeat_ms"] = heartbeatMs,
                ["light_writer_async"] = true,
            };
            var request = new LightWriteRequest(
                this.lightSnapshotPath,
                snapshot,
                signature,
                reason,
                seq,
                currentTick,
                buildWatch.Elapsed.TotalMilliseconds,
                totalWatch.Elapsed.TotalMilliseconds,
                writeReason,
                heartbeatDue,
                heartbeatMs);
            bool coalesced = this.EnqueueLightWrite(request);
            this.lastLightQueuedTick = currentTick;
            this.lastLightExportMetrics = this.LightQueuedMetrics(request, coalesced);
            return true;
        }
        catch (Exception ex)
        {
            this.Monitor.Log($"Failed to export NanaBridge light state: {ex}", LogLevel.Warn);
            return false;
        }
    }

    private void ExportHeavySnapshot(string reason)
    {
        try
        {
            var result = this.BuildSerializeWrite(
                reason,
                "heavy",
                this.snapshotPath,
                () => this.BuildSnapshot(reason));
            this.lastHeavyExportMetrics = result.Metrics;
            this.MaybeLogExportMetrics(result);
        }
        catch (Exception ex)
        {
            this.Monitor.Log($"Failed to export NanaBridge heavy state: {ex}", LogLevel.Warn);
        }
    }

    private void RecordHeavyDeferredMetric(uint tick)
    {
        var metrics = new Dictionary<string, object?>
        {
            ["export_kind"] = "heavy",
            ["export_reason"] = "heavy_deferred_due_to_light_write",
            ["export_tick"] = tick,
            ["heavy_deferred_due_to_light_write"] = true,
            ["completed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        };
        this.lastHeavyExportMetrics = metrics;
    }

    private ExportWriteResult BuildSerializeWrite(string reason, string exportKind, string path, Func<Dictionary<string, object?>> buildSnapshot)
    {
        uint seq = ++this.exportSeq;
        var totalWatch = Stopwatch.StartNew();
        var buildWatch = Stopwatch.StartNew();
        var snapshot = buildSnapshot();
        buildWatch.Stop();

        snapshot["export_seq"] = seq;
        snapshot["exportSeq"] = seq;
        snapshot["exportTick"] = Game1.ticks;
        snapshot["exportReason"] = reason;
        snapshot["exportKind"] = exportKind;
        snapshot["exportMetrics"] = new Dictionary<string, object?>
        {
            ["export_seq"] = seq,
            ["export_tick"] = Game1.ticks,
            ["export_kind"] = exportKind,
            ["export_reason"] = reason,
            ["build_snapshot_ms"] = Math.Round(buildWatch.Elapsed.TotalMilliseconds, 3),
        };

        var serializeWatch = Stopwatch.StartNew();
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(snapshot, JsonOptions);
        serializeWatch.Stop();

        var writeMetrics = this.WriteAtomicJson(path, json);
        totalWatch.Stop();

        var metrics = new Dictionary<string, object?>
        {
            ["state_json_bytes"] = exportKind == "heavy" ? json.Length : null,
            ["light_json_bytes"] = exportKind == "light" ? json.Length : null,
            ["payload_bytes"] = json.Length,
            ["build_snapshot_ms"] = Math.Round(buildWatch.Elapsed.TotalMilliseconds, 3),
            ["serialize_ms"] = Math.Round(serializeWatch.Elapsed.TotalMilliseconds, 3),
            ["write_ms"] = writeMetrics.TotalMs,
            ["temp_write_ms"] = writeMetrics.TempWriteMs,
            ["replace_ms"] = writeMetrics.ReplaceMs,
            ["cleanup_ms"] = writeMetrics.CleanupMs,
            ["export_total_ms"] = Math.Round(totalWatch.Elapsed.TotalMilliseconds, 3),
            ["export_reason"] = reason,
            ["export_kind"] = exportKind,
            ["export_tick"] = Game1.ticks,
            ["export_seq"] = seq,
            ["completed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        };
        return new ExportWriteResult(metrics);
    }

    private void MaybeLogExportMetrics(ExportWriteResult result)
    {
        uint tick = (uint)Math.Max(0, Game1.ticks);
        if (tick != 0 && tick - this.lastPerfLogTick < PerfLogIntervalTicks)
            return;
        this.lastPerfLogTick = tick;
        var metrics = result.Metrics;
        this.Monitor.Log(
            "NanaBridge export metrics: " +
            $"kind={metrics["export_kind"]} reason={metrics["export_reason"]} seq={metrics["export_seq"]} " +
            $"bytes={metrics["payload_bytes"]} build_ms={metrics["build_snapshot_ms"]} " +
            $"serialize_ms={metrics["serialize_ms"]} write_ms={metrics["write_ms"]} total_ms={metrics["export_total_ms"]}",
            LogLevel.Trace);
    }

    private WriteAtomicMetrics WriteAtomicJson(string path, byte[] json)
    {
        string directory = Path.GetDirectoryName(path) ?? this.dataDir;
        Directory.CreateDirectory(directory);
        var totalWatch = Stopwatch.StartNew();
        var cleanupWatch = Stopwatch.StartNew();
        this.CleanupStaleTempFiles(directory);
        cleanupWatch.Stop();

        string tempPath = Path.Combine(directory, $".{Path.GetFileName(path)}.{Guid.NewGuid():N}.tmp");
        double tempWriteMs = 0;
        double replaceMs = 0;

        try
        {
            var tempWriteWatch = Stopwatch.StartNew();
            using (var stream = new FileStream(tempPath, FileMode.CreateNew, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.None))
            {
                stream.Write(json, 0, json.Length);
            }
            tempWriteWatch.Stop();
            tempWriteMs = tempWriteWatch.Elapsed.TotalMilliseconds;

            var replaceWatch = Stopwatch.StartNew();
            if (File.Exists(path))
                File.Replace(tempPath, path, null, ignoreMetadataErrors: true);
            else
                File.Move(tempPath, path);
            replaceWatch.Stop();
            replaceMs = replaceWatch.Elapsed.TotalMilliseconds;
        }
        finally
        {
            if (File.Exists(tempPath))
                File.Delete(tempPath);
        }
        totalWatch.Stop();
        return new WriteAtomicMetrics(
            Math.Round(cleanupWatch.Elapsed.TotalMilliseconds, 3),
            Math.Round(tempWriteMs, 3),
            Math.Round(replaceMs, 3),
            Math.Round(totalWatch.Elapsed.TotalMilliseconds, 3));
    }

    private void CleanupStaleTempFiles(string? directory = null)
    {
        try
        {
            string dir = directory ?? this.dataDir;
            if (string.IsNullOrWhiteSpace(dir) || !Directory.Exists(dir))
                return;
            DateTime cutoff = DateTime.UtcNow.AddSeconds(-StaleTempCleanupSeconds);
            foreach (string path in Directory.EnumerateFiles(dir, ".state*.tmp").Concat(Directory.EnumerateFiles(dir, "state*.json.bak")))
            {
                try
                {
                    if (File.GetLastWriteTimeUtc(path) < cutoff)
                        File.Delete(path);
                }
                catch
                {
                    // Stale temp cleanup must never interfere with state export.
                }
            }
        }
        catch
        {
            // Export should remain best-effort even if cleanup fails.
        }
    }

    private bool EnqueueLightWrite(LightWriteRequest request)
    {
        bool coalesced = false;
        lock (this.lightWriterLock)
        {
            coalesced = this.pendingLightWrite != null || this.lightWriterRunning;
            this.pendingLightWrite = request;
            if (!this.lightWriterRunning)
            {
                this.lightWriterRunning = true;
                Task.Run(this.LightWriterLoop);
            }
        }
        return coalesced;
    }

    private void LightWriterLoop()
    {
        while (true)
        {
            LightWriteRequest? request;
            lock (this.lightWriterLock)
            {
                request = this.pendingLightWrite;
                this.pendingLightWrite = null;
                if (request == null)
                {
                    this.lightWriterRunning = false;
                    return;
                }
            }

            try
            {
                var serializeWatch = Stopwatch.StartNew();
                byte[] json = JsonSerializer.SerializeToUtf8Bytes(request.Snapshot, JsonOptions);
                serializeWatch.Stop();
                var writeMetrics = this.WriteAtomicJson(request.Path, json);
                var totalMs = Math.Round(request.GameThreadTotalMs + serializeWatch.Elapsed.TotalMilliseconds + writeMetrics.TotalMs, 3);
                var metrics = new Dictionary<string, object?>
                {
                    ["state_json_bytes"] = null,
                    ["light_json_bytes"] = json.Length,
                    ["payload_bytes"] = json.Length,
                    ["build_snapshot_ms"] = Math.Round(request.BuildSnapshotMs, 3),
                    ["serialize_ms"] = Math.Round(serializeWatch.Elapsed.TotalMilliseconds, 3),
                    ["write_ms"] = writeMetrics.TotalMs,
                    ["temp_write_ms"] = writeMetrics.TempWriteMs,
                    ["replace_ms"] = writeMetrics.ReplaceMs,
                    ["cleanup_ms"] = writeMetrics.CleanupMs,
                    ["export_total_ms"] = totalMs,
                    ["export_reason"] = request.Reason,
                    ["export_kind"] = "light",
                    ["export_tick"] = request.ExportTick,
                    ["export_seq"] = request.ExportSeq,
                    ["completed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    ["light_writer_async"] = true,
                    ["light_export_skipped"] = false,
                    ["light_write_reason"] = request.WriteReason,
                    ["heartbeat_due"] = request.HeartbeatDue,
                    ["light_heartbeat_ms"] = request.HeartbeatMs,
                };
                lock (this.lightWriterLock)
                {
                    this.lastLightSignature = request.Signature;
                    this.lastLightWriteCompletedUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    this.lastLightWriteCompletedTick = request.ExportTick;
                    this.lastLightExportMetrics = metrics;
                }
                this.MaybeLogExportMetrics(new ExportWriteResult(metrics));
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"Failed to write NanaBridge light state async: {ex.GetType().Name}: {ex.Message}", LogLevel.Warn);
            }
        }
    }

    private Dictionary<string, object?> LightQueuedMetrics(LightWriteRequest request, bool coalesced)
    {
        return new Dictionary<string, object?>
        {
            ["export_kind"] = "light",
            ["export_reason"] = request.Reason,
            ["export_tick"] = request.ExportTick,
            ["export_seq"] = request.ExportSeq,
            ["build_snapshot_ms"] = Math.Round(request.BuildSnapshotMs, 3),
            ["export_total_ms"] = Math.Round(request.GameThreadTotalMs, 3),
            ["light_writer_async"] = true,
            ["light_writer_busy"] = coalesced,
            ["light_write_coalesced"] = coalesced,
            ["light_export_skipped"] = false,
            ["heartbeat_due"] = request.HeartbeatDue,
            ["light_heartbeat_ms"] = request.HeartbeatMs,
            ["light_write_queued"] = true,
            ["light_write_reason"] = request.WriteReason,
            ["last_light_write_age_ms"] = this.LastLightWriteAgeMs(),
            ["completed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        };
    }

    private Dictionary<string, object?> LightSkippedMetrics(string reason, uint seq, uint tick, bool heartbeatDue, int heartbeatMs, double buildSnapshotMs, double totalMs)
    {
        return new Dictionary<string, object?>
        {
            ["export_kind"] = "light",
            ["export_reason"] = reason,
            ["export_tick"] = tick,
            ["export_seq"] = seq,
            ["build_snapshot_ms"] = Math.Round(buildSnapshotMs, 3),
            ["export_total_ms"] = Math.Round(totalMs, 3),
            ["light_writer_async"] = true,
            ["light_export_skipped"] = true,
            ["light_skip_reason"] = "unchanged",
            ["heartbeat_due"] = heartbeatDue,
            ["light_heartbeat_ms"] = heartbeatMs,
            ["last_light_write_age_ms"] = this.LastLightWriteAgeMs(),
            ["completed_at_unix_ms"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        };
    }

    private long? LastLightWriteAgeMs()
    {
        if (this.lastLightWriteCompletedUnixMs <= 0)
            return null;
        return Math.Max(0, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - this.lastLightWriteCompletedUnixMs);
    }

    private int ConfigInt(string key, int defaultValue, int minValue, int maxValue)
    {
        try
        {
            string? raw = Environment.GetEnvironmentVariable(key);
            if (int.TryParse(raw, out int value))
                return Math.Max(minValue, Math.Min(maxValue, value));
        }
        catch
        {
            // Environment reads are best-effort only.
        }
        return defaultValue;
    }

    private string BuildLightSignature(Dictionary<string, object?> snapshot)
    {
        var parts = new List<string>();
        var game = snapshot.GetValueOrDefault("game") as Dictionary<string, object?>;
        var player = snapshot.GetValueOrDefault("player") as Dictionary<string, object?>;
        var safety = snapshot.GetValueOrDefault("safety") as Dictionary<string, object?>;
        var ui = snapshot.GetValueOrDefault("ui") as Dictionary<string, object?>;
        var control = snapshot.GetValueOrDefault("control") as Dictionary<string, object?>;
        parts.Add(ValueForSignature(game?.GetValueOrDefault("location")));
        parts.Add(ValueForSignature(player?.GetValueOrDefault("tile")));
        parts.Add(ValueForSignature(player?.GetValueOrDefault("pixel")));
        parts.Add(ValueForSignature(player?.GetValueOrDefault("facing")));
        parts.Add(ValueForSignature(player?.GetValueOrDefault("moving")));
        parts.Add(ValueForSignature(player?.GetValueOrDefault("canMove")));
        foreach (var key in new[] { "menuOpen", "playerCanMove", "dangerNearby", "combat", "isSaving" })
            parts.Add(ValueForSignature(safety?.GetValueOrDefault(key)));
        foreach (var key in new[] { "activeMenu", "dialogueOpen", "minigame" })
            parts.Add(ValueForSignature(ui?.GetValueOrDefault(key)));
        parts.Add(ValueForSignature(control?.GetValueOrDefault("allowsInput")));
        parts.Add(ValueForSignature(control?.GetValueOrDefault("bridgeCanPressKeys")));
        parts.Add(this.LightNpcSignature(snapshot));
        return string.Join("|", parts);
    }

    private string LightNpcSignature(Dictionary<string, object?> snapshot)
    {
        if (snapshot.GetValueOrDefault("entities") is not Dictionary<string, object?> entities)
            return "";
        if (entities.GetValueOrDefault("npcs") is not IEnumerable npcs)
            return "";
        var parts = new List<string>();
        foreach (object npc in npcs)
        {
            if (npc is not Dictionary<string, object?> item)
                continue;
            parts.Add($"{item.GetValueOrDefault("name")}:{ValueForSignature(item.GetValueOrDefault("tile"))}:{item.GetValueOrDefault("facing")}");
        }
        parts.Sort(StringComparer.Ordinal);
        return string.Join(",", parts);
    }

    private static string ValueForSignature(object? value)
    {
        if (value == null)
            return "";
        if (value is IEnumerable enumerable && value is not string)
        {
            var parts = new List<string>();
            foreach (object? item in enumerable)
                parts.Add(ValueForSignature(item));
            return "[" + string.Join(",", parts) + "]";
        }
        if (value is float f)
            return Math.Round(f / LightPixelBucket).ToString();
        if (value is double d)
            return Math.Round(d / LightPixelBucket).ToString();
        return value.ToString() ?? "";
    }

    private Dictionary<string, object?> BuildSnapshot(string reason)
    {
        bool worldReady = Context.IsWorldReady;
        Farmer? player = worldReady ? Game1.player : null;
        GameLocation? location = worldReady ? Game1.currentLocation : null;
        Vector2 playerTile = player?.Tile ?? Vector2.Zero;
        Vector2 frontTile = worldReady && player != null ? this.GetFrontTile(playerTile, player.FacingDirection) : Vector2.Zero;
        var map = location != null ? this.BuildMap(location, player) : this.EmptyMap();
        var nearbyTiles = worldReady && location != null && player != null
            ? this.BuildNearbyTiles(location, player, radius: 4).ToList()
            : new List<object>();

        return new Dictionary<string, object?>
        {
            ["schemaVersion"] = SchemaVersion,
            ["source"] = "NanaBridge",
            ["readOnly"] = true,
            ["generatedAtUnix"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
            ["exportReason"] = reason,
            ["game"] = new Dictionary<string, object?>
            {
                ["version"] = Game1.version,
                ["smapiVersion"] = this.Helper.ModRegistry.ModID,
                ["saveLoaded"] = worldReady,
                ["isMainPlayer"] = Context.IsMainPlayer,
                ["timeOfDay"] = worldReady ? Game1.timeOfDay : 0,
                ["season"] = worldReady ? Game1.currentSeason : null,
                ["dayOfMonth"] = worldReady ? Game1.dayOfMonth : 0,
                ["weather"] = worldReady ? this.GetWeatherName() : null,
                ["money"] = player?.Money ?? 0,
                ["mapWidth"] = map["width"],
                ["mapHeight"] = map["height"],
                ["location"] = map["name"],
            },
            ["player"] = this.BuildPlayer(player, location),
            ["frontTile"] = worldReady && location != null && player != null ? this.BuildTileInfo(location, frontTile, player) : this.EmptyTile(frontTile),
            ["nearbyTiles"] = nearbyTiles,
            ["map"] = map,
            ["passableMatrix"] = map["passableMatrix"],
            ["blockedMatrix"] = map["blockedMatrix"],
            ["fullTiles"] = map["fullTiles"],
            ["warps"] = map["warps"],
            ["buildings"] = map["buildings"],
            ["entities"] = this.BuildEntities(location, player),
            ["inventory"] = this.BuildInventory(player),
            ["safety"] = new Dictionary<string, object?>
            {
                ["menuOpen"] = Game1.activeClickableMenu != null,
                ["playerCanMove"] = worldReady && Game1.activeClickableMenu == null && Game1.player.CanMove,
                ["isFestival"] = worldReady && Utility.isFestivalDay(Game1.dayOfMonth, Game1.season),
                ["dangerNearby"] = worldReady && location != null && this.GetMonsters(location, player).Any(m => m.Distance <= 3),
                ["lowStamina"] = worldReady && player != null && player.Stamina / Math.Max(1f, player.MaxStamina) < 0.18f,
                ["inventoryFull"] = worldReady && player != null && player.freeSpotsInInventory() <= 0,
            },
            ["ui"] = this.BuildUiState(),
            ["control"] = new Dictionary<string, object?>
            {
                ["allowsInput"] = false,
                ["bridgeCanPressKeys"] = false,
                ["nanaInputOwner"] = "python_guarded_executor",
            },
        };
    }

    private Dictionary<string, object?> BuildUiState()
    {
        object? menu = Game1.activeClickableMenu;
        string? menuName = menu?.GetType().Name;
        string? menuFullName = menu?.GetType().FullName;
        object? okButton = menu != null ? this.ReadMember(menu, "okButton") : null;
        object? upperRightCloseButton = menu != null ? this.ReadMember(menu, "upperRightCloseButton") : null;
        var okButtonBounds = this.ReadBounds(okButton);
        var upperRightCloseButtonBounds = this.ReadBounds(upperRightCloseButton);
        bool itemGrabberOpen = string.Equals(menuName, "ItemGrabMenu", StringComparison.OrdinalIgnoreCase)
            || string.Equals(menuName, "ItemGrabberMenu", StringComparison.OrdinalIgnoreCase)
            || menuFullName?.Contains("ItemGrab", StringComparison.OrdinalIgnoreCase) == true;
        bool shippingBinContext = itemGrabberOpen && Context.IsWorldReady && Game1.player != null && this.PlayerFacesShippingBin(Game1.player);

        return new Dictionary<string, object?>
        {
            ["activeMenu"] = menuName,
            ["activeMenuName"] = menuName,
            ["activeClickableMenuName"] = menuName,
            ["activeClickableMenuFullName"] = menuFullName,
            ["menuName"] = menuName,
            ["dialogueOpen"] = Game1.dialogueUp,
            ["shippingBinOpen"] = menuName?.Contains("Shipping", StringComparison.OrdinalIgnoreCase) == true,
            ["shippingBinContext"] = shippingBinContext,
            ["itemGrabberOpen"] = itemGrabberOpen,
            ["inventoryGridOpen"] = itemGrabberOpen,
            ["hasOkButton"] = okButtonBounds != null,
            ["okButtonBounds"] = okButtonBounds,
            ["okButtonCenter"] = this.CenterOf(okButtonBounds),
            ["hasUpperRightCloseButton"] = upperRightCloseButtonBounds != null,
            ["upperRightCloseButtonBounds"] = upperRightCloseButtonBounds,
            ["upperRightCloseButtonCenter"] = this.CenterOf(upperRightCloseButtonBounds),
            ["uiScale"] = this.ReadOptionsDouble("uiScale"),
            ["zoomLevel"] = this.ReadOptionsDouble("zoomLevel"),
            ["viewport"] = this.RectangleInfo(Game1.viewport),
            ["uiViewport"] = this.ReadGame1Rectangle("uiViewport"),
            ["graphicsViewport"] = this.GraphicsViewportInfo(),
            ["minigame"] = Game1.currentMinigame?.GetType().Name,
        };
    }

    private bool PlayerFacesShippingBin(Farmer player)
    {
        try
        {
            if (Game1.currentLocation == null)
                return false;
            Vector2 frontTile = this.GetFrontTile(player.Tile, player.FacingDirection);
            foreach (object building in this.EnumerateBuildings(Game1.currentLocation))
            {
                string? type = this.ReadString(building, "buildingType") ?? building.GetType().Name;
                if (type == null || !type.Contains("Shipping", StringComparison.OrdinalIgnoreCase))
                    continue;
                int x = this.ReadInt(building, "tileX");
                int y = this.ReadInt(building, "tileY");
                int width = Math.Max(1, this.ReadInt(building, "tilesWide"));
                int height = Math.Max(1, this.ReadInt(building, "tilesHigh"));
                if ((int)frontTile.X >= x && (int)frontTile.X < x + width && (int)frontTile.Y >= y && (int)frontTile.Y < y + height)
                    return true;
            }
        }
        catch
        {
            return false;
        }
        return false;
    }

    private object? CenterOf(Dictionary<string, object?>? bounds)
    {
        if (bounds == null)
            return null;
        try
        {
            int x = Convert.ToInt32(bounds["x"]);
            int y = Convert.ToInt32(bounds["y"]);
            int width = Convert.ToInt32(bounds["width"]);
            int height = Convert.ToInt32(bounds["height"]);
            return new[] { x + width / 2, y + height / 2 };
        }
        catch
        {
            return null;
        }
    }

    private Dictionary<string, object?>? ReadBounds(object? target)
    {
        if (target == null)
            return null;
        object? bounds = this.ReadMember(target, "bounds") ?? target;
        if (bounds is Microsoft.Xna.Framework.Rectangle rectangle)
            return this.RectangleInfo(rectangle);
        if (bounds is xTile.Dimensions.Rectangle tileRectangle)
            return this.RectangleInfo(tileRectangle);
        int? x = this.ReadNullableInt(bounds, "X") ?? this.ReadNullableInt(bounds, "x");
        int? y = this.ReadNullableInt(bounds, "Y") ?? this.ReadNullableInt(bounds, "y");
        int? width = this.ReadNullableInt(bounds, "Width") ?? this.ReadNullableInt(bounds, "width");
        int? height = this.ReadNullableInt(bounds, "Height") ?? this.ReadNullableInt(bounds, "height");
        if (x == null || y == null || width == null || height == null || width <= 0 || height <= 0)
            return null;
        return new Dictionary<string, object?>
        {
            ["x"] = x.Value,
            ["y"] = y.Value,
            ["width"] = width.Value,
            ["height"] = height.Value,
        };
    }

    private Dictionary<string, object?> RectangleInfo(Microsoft.Xna.Framework.Rectangle rectangle)
    {
        return new Dictionary<string, object?>
        {
            ["x"] = rectangle.X,
            ["y"] = rectangle.Y,
            ["width"] = rectangle.Width,
            ["height"] = rectangle.Height,
        };
    }

    private Dictionary<string, object?> RectangleInfo(xTile.Dimensions.Rectangle rectangle)
    {
        return new Dictionary<string, object?>
        {
            ["x"] = rectangle.X,
            ["y"] = rectangle.Y,
            ["width"] = rectangle.Width,
            ["height"] = rectangle.Height,
        };
    }

    private int? ReadNullableInt(object target, string name)
    {
        object? value = this.UnwrapValue(this.ReadMember(target, name));
        return value switch
        {
            int i => i,
            float f => (int)f,
            double d => (int)d,
            _ => null,
        };
    }

    private double? ReadOptionsDouble(string name)
    {
        try
        {
            if (Game1.options == null)
                return null;
            object? value = this.UnwrapValue(this.ReadMember(Game1.options, name));
            return value switch
            {
                double d => d,
                float f => f,
                int i => i,
                _ => null,
            };
        }
        catch
        {
            return null;
        }
    }

    private object? ReadGame1Rectangle(string name)
    {
        try
        {
            object? value = this.ReadStaticMember(typeof(Game1), name);
            if (value is Microsoft.Xna.Framework.Rectangle rectangle)
                return this.RectangleInfo(rectangle);
            if (value is xTile.Dimensions.Rectangle tileRectangle)
                return this.RectangleInfo(tileRectangle);
        }
        catch
        {
            return null;
        }
        return null;
    }

    private object? GraphicsViewportInfo()
    {
        try
        {
            var viewport = Game1.graphics.GraphicsDevice.Viewport;
            return new Dictionary<string, object?>
            {
                ["x"] = viewport.X,
                ["y"] = viewport.Y,
                ["width"] = viewport.Width,
                ["height"] = viewport.Height,
            };
        }
        catch
        {
            return null;
        }
    }

    private Dictionary<string, object?> BuildLightSnapshot(string reason)
    {
        bool worldReady = Context.IsWorldReady;
        Farmer? player = worldReady ? Game1.player : null;
        GameLocation? location = worldReady ? Game1.currentLocation : null;
        Vector2 playerTile = player?.Tile ?? Vector2.Zero;
        Vector2 frontTile = worldReady && player != null ? this.GetFrontTile(playerTile, player.FacingDirection) : Vector2.Zero;
        int mapWidth = location != null ? this.GetMapWidth(location) : 0;
        int mapHeight = location != null ? this.GetMapHeight(location) : 0;

        return new Dictionary<string, object?>
        {
            ["schemaVersion"] = SchemaVersion,
            ["source"] = "NanaBridge",
            ["readOnly"] = true,
            ["generatedAtUnix"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0,
            ["generatedAtUnixMs"] = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            ["exportReason"] = reason,
            ["exportKind"] = "light",
            ["lightHeartbeatMs"] = this.ConfigInt("NANA_BRIDGE_LIGHT_HEARTBEAT_MS", DefaultLightHeartbeatMs, 100, 2000),
            ["game"] = new Dictionary<string, object?>
            {
                ["version"] = Game1.version,
                ["saveLoaded"] = worldReady,
                ["isMainPlayer"] = Context.IsMainPlayer,
                ["timeOfDay"] = worldReady ? Game1.timeOfDay : 0,
                ["season"] = worldReady ? Game1.currentSeason : null,
                ["dayOfMonth"] = worldReady ? Game1.dayOfMonth : 0,
                ["location"] = location?.NameOrUniqueName,
                ["mapWidth"] = mapWidth,
                ["mapHeight"] = mapHeight,
                ["isSaving"] = this.IsSaving(),
            },
            ["map"] = new Dictionary<string, object?>
            {
                ["name"] = location?.NameOrUniqueName,
                ["id"] = location?.uniqueName.Value ?? location?.Name,
                ["width"] = mapWidth,
                ["height"] = mapHeight,
                ["mapWidth"] = mapWidth,
                ["mapHeight"] = mapHeight,
            },
            ["player"] = this.BuildLightPlayer(player, location),
            ["frontTile"] = worldReady && location != null && player != null ? this.BuildTileInfo(location, frontTile, player) : this.EmptyTile(frontTile),
            ["entities"] = this.BuildLightEntities(location, player),
            ["safety"] = new Dictionary<string, object?>
            {
                ["menuOpen"] = Game1.activeClickableMenu != null,
                ["playerCanMove"] = worldReady && Game1.activeClickableMenu == null && Game1.player.CanMove,
                ["canMove"] = worldReady && Game1.activeClickableMenu == null && Game1.player.CanMove,
                ["isFestival"] = worldReady && Utility.isFestivalDay(Game1.dayOfMonth, Game1.season),
                ["dangerNearby"] = worldReady && location != null && this.GetMonsters(location, player).Any(m => m.Distance <= 3),
                ["combat"] = worldReady && location != null && this.GetMonsters(location, player).Any(m => m.Distance <= 3),
                ["isSaving"] = this.IsSaving(),
            },
            ["ui"] = this.BuildLightUiState(),
            ["control"] = new Dictionary<string, object?>
            {
                ["allowsInput"] = false,
                ["bridgeCanPressKeys"] = false,
                ["nanaInputOwner"] = "python_guarded_executor",
            },
            ["heavySnapshot"] = this.HeavySnapshotSummary(),
            ["lastLightExportMetrics"] = this.lastLightExportMetrics,
            ["lastHeavyExportMetrics"] = this.lastHeavyExportMetrics,
        };
    }

    private Dictionary<string, object?> BuildLightPlayer(Farmer? player, GameLocation? location)
    {
        if (player == null)
        {
            return new Dictionary<string, object?>
            {
                ["location"] = null,
                ["tile"] = new[] { 0, 0 },
                ["pixel"] = new[] { 0f, 0f },
                ["facing"] = "unknown",
                ["moving"] = false,
                ["canMove"] = false,
            };
        }

        return new Dictionary<string, object?>
        {
            ["location"] = location?.NameOrUniqueName,
            ["tile"] = new[] { (int)player.Tile.X, (int)player.Tile.Y },
            ["pixel"] = new[] { player.Position.X, player.Position.Y },
            ["facing"] = this.DirectionName(player.FacingDirection),
            ["moving"] = this.PlayerAppearsMoving(player),
            ["canMove"] = player.CanMove,
            ["selectedSlot"] = player.CurrentToolIndex,
        };
    }

    private bool PlayerAppearsMoving(Farmer player)
    {
        try
        {
            return Math.Abs(player.xVelocity) > 0.01f || Math.Abs(player.yVelocity) > 0.01f;
        }
        catch
        {
            return false;
        }
    }

    private Dictionary<string, object?> BuildLightUiState()
    {
        string? menuName = Game1.activeClickableMenu?.GetType().Name;
        return new Dictionary<string, object?>
        {
            ["activeMenu"] = menuName,
            ["activeMenuName"] = menuName,
            ["menuOpen"] = Game1.activeClickableMenu != null,
            ["dialogueOpen"] = Game1.dialogueUp,
            ["minigame"] = Game1.currentMinigame?.GetType().Name,
        };
    }

    private object BuildLightEntities(GameLocation? location, Farmer? player)
    {
        return new Dictionary<string, object?>
        {
            ["monsters"] = this.GetMonsters(location, player).Select(m => new Dictionary<string, object?>
            {
                ["name"] = m.Name,
                ["type"] = m.Type,
                ["tile"] = new[] { (int)m.Tile.X, (int)m.Tile.Y },
                ["distance"] = Math.Round(m.Distance, 2),
                ["danger"] = m.Distance <= 3 ? "near" : "far",
            }).ToList(),
            ["npcs"] = (object)(location?.characters
                .Where(npc => npc is not Monster)
                .Select(npc => new Dictionary<string, object?>
                {
                    ["id"] = npc.Name,
                    ["name"] = npc.Name,
                    ["tile"] = new[] { (int)npc.Tile.X, (int)npc.Tile.Y },
                    ["facing"] = this.DirectionName(npc.FacingDirection),
                    ["distance"] = player != null ? Math.Round(Vector2.Distance(player.Tile, npc.Tile), 2) : 0,
                    ["canTalk"] = npc is NPC,
                    ["isVillager"] = npc is NPC,
                }).Cast<object>().ToList() ?? new List<object>()),
        };
    }

    private Dictionary<string, object?> HeavySnapshotSummary()
    {
        return new Dictionary<string, object?>
        {
            ["path"] = this.snapshotPath,
            ["exists"] = File.Exists(this.snapshotPath),
            ["lastLocation"] = this.lastHeavyLocation,
            ["lastMetrics"] = this.lastHeavyExportMetrics,
        };
    }

    private Dictionary<string, object?> EmptyMap()
    {
        return new Dictionary<string, object?>
        {
            ["name"] = null,
            ["id"] = null,
            ["width"] = 0,
            ["height"] = 0,
            ["passableMatrix"] = new List<List<bool>>(),
            ["blockedMatrix"] = new List<List<bool>>(),
            ["fullTiles"] = new List<object>(),
            ["warps"] = new List<object>(),
            ["buildings"] = new List<object>(),
            ["doors"] = new List<object>(),
        };
    }

    private Dictionary<string, object?> BuildMap(GameLocation location, Farmer? player)
    {
        int width = this.GetMapWidth(location);
        int height = this.GetMapHeight(location);
        var passableMatrix = new List<List<bool>>(height);
        var blockedMatrix = new List<List<bool>>(height);
        var fullTiles = new List<object>(Math.Max(0, width * height));

        for (int y = 0; y < height; y++)
        {
            var passableRow = new List<bool>(width);
            var blockedRow = new List<bool>(width);
            for (int x = 0; x < width; x++)
            {
                Vector2 tile = new(x, y);
                bool passable = this.IsTilePassable(location, x, y);
                passableRow.Add(passable);
                blockedRow.Add(!passable);
                fullTiles.Add(this.BuildTileInfo(location, tile, player, passable));
            }
            passableMatrix.Add(passableRow);
            blockedMatrix.Add(blockedRow);
        }

        return new Dictionary<string, object?>
        {
            ["name"] = location.NameOrUniqueName,
            ["id"] = location.uniqueName.Value ?? location.Name,
            ["width"] = width,
            ["height"] = height,
            ["mapWidth"] = width,
            ["mapHeight"] = height,
            ["passableMatrix"] = passableMatrix,
            ["blockedMatrix"] = blockedMatrix,
            ["fullTiles"] = fullTiles,
            ["warps"] = this.BuildWarps(location),
            ["buildings"] = this.BuildBuildings(location),
            ["doors"] = this.BuildDoors(location),
        };
    }

    private int GetMapWidth(GameLocation location)
    {
        try
        {
            return location.Map?.Layers?.FirstOrDefault()?.LayerWidth ?? 0;
        }
        catch
        {
            return 0;
        }
    }

    private int GetMapHeight(GameLocation location)
    {
        try
        {
            return location.Map?.Layers?.FirstOrDefault()?.LayerHeight ?? 0;
        }
        catch
        {
            return 0;
        }
    }

    private bool IsTilePassable(GameLocation location, int x, int y)
    {
        try
        {
            return location.isTilePassable(new Location(x, y), Game1.viewport);
        }
        catch
        {
            return false;
        }
    }

    private List<object> BuildWarps(GameLocation location)
    {
        var result = new List<object>();
        foreach (var warp in location.warps)
        {
            result.Add(new Dictionary<string, object?>
            {
                ["tile"] = new[] { warp.X, warp.Y },
                ["x"] = warp.X,
                ["y"] = warp.Y,
                ["target"] = warp.TargetName,
                ["targetName"] = warp.TargetName,
                ["targetTile"] = new[] { warp.TargetX, warp.TargetY },
                ["targetX"] = warp.TargetX,
                ["targetY"] = warp.TargetY,
            });
        }
        return result;
    }

    private List<object> BuildDoors(GameLocation location)
    {
        var result = new List<object>();
        foreach (var warp in location.warps)
        {
            result.Add(new Dictionary<string, object?>
            {
                ["tile"] = new[] { warp.X, warp.Y },
                ["kind"] = "warp",
                ["target"] = warp.TargetName,
                ["targetTile"] = new[] { warp.TargetX, warp.TargetY },
            });
        }
        return result;
    }

    private List<object> BuildBuildings(GameLocation location)
    {
        var result = new List<object>();
        foreach (object building in this.EnumerateBuildings(location))
        {
            int x = this.ReadInt(building, "tileX");
            int y = this.ReadInt(building, "tileY");
            int width = this.ReadInt(building, "tilesWide");
            int height = this.ReadInt(building, "tilesHigh");
            result.Add(new Dictionary<string, object?>
            {
                ["type"] = this.ReadString(building, "buildingType") ?? building.GetType().Name,
                ["tile"] = new[] { x, y },
                ["x"] = x,
                ["y"] = y,
                ["width"] = width,
                ["height"] = height,
                ["doorTile"] = this.ReadPoint(building, "humanDoor"),
                ["indoors"] = this.ReadNestedName(building, "indoors"),
            });
        }
        return result;
    }

    private IEnumerable<object> EnumerateBuildings(GameLocation location)
    {
        object? buildings = this.ReadMember(location, "buildings");
        if (buildings is not IEnumerable enumerable)
            yield break;
        foreach (object building in enumerable)
            yield return building;
    }

    private object? ReadMember(object target, string name)
    {
        Type type = target.GetType();
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        FieldInfo? field = type.GetField(name, flags);
        if (field != null)
            return field.GetValue(target);
        PropertyInfo? property = type.GetProperty(name, flags);
        return property?.GetValue(target);
    }

    private object? ReadStaticMember(Type type, string name)
    {
        const BindingFlags flags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        FieldInfo? field = type.GetField(name, flags);
        if (field != null)
            return field.GetValue(null);
        PropertyInfo? property = type.GetProperty(name, flags);
        return property?.GetValue(null);
    }

    private bool IsSaving()
    {
        try
        {
            object? value = this.ReadStaticMember(typeof(Game1), "isSaving") ?? this.ReadStaticMember(typeof(Game1), "IsSaving");
            value = this.UnwrapValue(value);
            return value is bool saving && saving;
        }
        catch
        {
            return false;
        }
    }

    private object? UnwrapValue(object? value)
    {
        if (value == null)
            return null;
        object? netValue = this.ReadMember(value, "Value");
        return netValue ?? value;
    }

    private int ReadInt(object target, string name)
    {
        object? value = this.UnwrapValue(this.ReadMember(target, name));
        return value switch
        {
            int i => i,
            float f => (int)f,
            double d => (int)d,
            _ => 0,
        };
    }

    private string? ReadString(object target, string name)
    {
        object? value = this.UnwrapValue(this.ReadMember(target, name));
        return value?.ToString();
    }

    private int[]? ReadPoint(object target, string name)
    {
        object? value = this.UnwrapValue(this.ReadMember(target, name));
        if (value is Point p)
            return new[] { p.X, p.Y };
        if (value is Vector2 v)
            return new[] { (int)v.X, (int)v.Y };
        return null;
    }

    private string? ReadNestedName(object target, string name)
    {
        object? value = this.UnwrapValue(this.ReadMember(target, name));
        if (value == null)
            return null;
        object? nested = this.ReadMember(value, "NameOrUniqueName") ?? this.ReadMember(value, "Name");
        return nested?.ToString() ?? value.ToString();
    }

    private object BuildPlayer(Farmer? player, GameLocation? location)
    {
        if (player == null)
        {
            return new Dictionary<string, object?>
            {
                ["location"] = null,
                ["tile"] = new[] { 0, 0 },
                ["pixel"] = new[] { 0f, 0f },
                ["facing"] = "unknown",
                ["stamina"] = new { current = 0, max = 0, ratio = 0.0 },
                ["health"] = new { current = 0, max = 0, ratio = 0.0 },
                ["currentTool"] = null,
                ["selectedSlot"] = 0,
            };
        }

        return new Dictionary<string, object?>
        {
            ["location"] = location?.NameOrUniqueName,
            ["tile"] = new[] { (int)player.Tile.X, (int)player.Tile.Y },
            ["pixel"] = new[] { player.Position.X, player.Position.Y },
            ["facing"] = this.DirectionName(player.FacingDirection),
            ["stamina"] = new Dictionary<string, object?>
            {
                ["current"] = Math.Round((double)player.Stamina, 2),
                ["max"] = Math.Round((double)player.MaxStamina, 2),
                ["ratio"] = Math.Round((double)(player.Stamina / Math.Max(1f, player.MaxStamina)), 3),
            },
            ["health"] = new Dictionary<string, object?>
            {
                ["current"] = player.health,
                ["max"] = player.maxHealth,
                ["ratio"] = Math.Round(player.health / Math.Max(1.0, player.maxHealth), 3),
            },
            ["currentTool"] = this.ItemInfo(player.CurrentItem),
            ["selectedSlot"] = player.CurrentToolIndex,
        };
    }

    private object BuildInventory(Farmer? player)
    {
        if (player == null)
            return new { items = Array.Empty<object>(), freeSlots = 0, selectedSlot = 0 };

        var items = new List<object>();
        for (int i = 0; i < player.Items.Count; i++)
        {
            StardewValley.Item? item = player.Items[i];
            if (item == null)
                continue;
            if (this.ItemInfo(item) is Dictionary<string, object?> info)
            {
                info["slot"] = i;
                items.Add(info);
            }
        }

        return new Dictionary<string, object?>
        {
            ["items"] = items,
            ["freeSlots"] = player.freeSpotsInInventory(),
            ["selectedSlot"] = player.CurrentToolIndex,
        };
    }

    private object? ItemInfo(StardewValley.Item? item)
    {
        if (item == null)
            return null;

        return new Dictionary<string, object?>
        {
            ["qualifiedId"] = item.QualifiedItemId,
            ["name"] = item.Name,
            ["displayName"] = item.DisplayName,
            ["category"] = item.Category,
            ["count"] = item.Stack,
            ["type"] = item.GetType().Name,
        };
    }

    private IEnumerable<object> BuildNearbyTiles(GameLocation location, Farmer player, int radius)
    {
        Vector2 center = player.Tile;
        for (int y = (int)center.Y - radius; y <= (int)center.Y + radius; y++)
        {
            for (int x = (int)center.X - radius; x <= (int)center.X + radius; x++)
            {
                Vector2 tile = new(x, y);
                if (Vector2.Distance(center, tile) > radius)
                    continue;
                yield return this.BuildTileInfo(location, tile, player);
            }
        }
    }

    private object BuildTileInfo(GameLocation location, Vector2 tile, Farmer? player = null, bool? knownPassable = null)
    {
        string kind = "clear";
        string? sourceType = null;
        string? qualifiedId = null;
        bool actionable = false;
        string recommendedAction = "none";
        string? recommendedTool = null;
        var extra = new Dictionary<string, object?>();

        if (location.objects.TryGetValue(tile, out var obj))
        {
            sourceType = "Object";
            qualifiedId = obj.QualifiedItemId;
            kind = this.ClassifyObject(obj);
            actionable = kind is "stone" or "ore" or "forage" or "machine" or "chest";
            (recommendedAction, recommendedTool) = kind switch
            {
                "stone" or "ore" => ("pickaxe", "pickaxe"),
                "forage" or "machine" or "chest" => ("interact", null),
                _ => ("none", null),
            };
        }
        else if (location.terrainFeatures.TryGetValue(tile, out var feature))
        {
            sourceType = feature.GetType().Name;
            switch (feature)
            {
                case Grass grass:
                    kind = "grass";
                    actionable = true;
                    recommendedAction = "scythe";
                    recommendedTool = "scythe";
                    qualifiedId = grass.GetType().FullName;
                    break;
                case Tree tree:
                    kind = "tree";
                    actionable = true;
                    recommendedAction = "axe";
                    recommendedTool = "axe";
                    qualifiedId = tree.GetType().FullName;
                    break;
                case HoeDirt dirt:
                    kind = dirt.crop != null ? "crop" : "soil";
                    actionable = true;
                    recommendedAction = dirt.crop != null && dirt.crop.currentPhase.Value >= dirt.crop.phaseDays.Count - 1 ? "harvest" : (dirt.state.Value == HoeDirt.watered ? "none" : "water");
                    recommendedTool = recommendedAction == "water" ? "watering_can" : null;
                    qualifiedId = dirt.crop?.netSeedIndex.Value;
                    extra["watered"] = dirt.state.Value == HoeDirt.watered;
                    extra["harvestable"] = dirt.crop != null && dirt.crop.currentPhase.Value >= dirt.crop.phaseDays.Count - 1;
                    break;
                default:
                    kind = "terrain";
                    qualifiedId = feature.GetType().FullName;
                    break;
            }
        }

        bool passable = knownPassable ?? this.IsTilePassable(location, (int)tile.X, (int)tile.Y);
        if (!passable && kind == "clear")
            kind = "blocked";

        var result = new Dictionary<string, object?>
        {
            ["tile"] = new[] { (int)tile.X, (int)tile.Y },
            ["x"] = (int)tile.X,
            ["y"] = (int)tile.Y,
            ["kind"] = kind,
            ["passable"] = passable,
            ["blocked"] = !passable,
            ["actionable"] = actionable,
            ["recommendedAction"] = recommendedAction,
            ["recommendedTool"] = recommendedTool,
            ["confidence"] = 1.0,
            ["sourceType"] = sourceType,
            ["qualifiedId"] = qualifiedId,
            ["distance"] = player != null ? Math.Round(Vector2.Distance(player.Tile, tile), 2) : null,
        };
        foreach (var pair in extra)
            result[pair.Key] = pair.Value;
        return result;
    }

    private object EmptyTile(Vector2 tile)
    {
        return new Dictionary<string, object?>
        {
            ["tile"] = new[] { (int)tile.X, (int)tile.Y },
            ["kind"] = "unknown",
            ["passable"] = false,
            ["blocked"] = true,
            ["actionable"] = false,
            ["recommendedAction"] = "none",
            ["recommendedTool"] = null,
            ["confidence"] = 0.0,
        };
    }

    private object BuildEntities(GameLocation? location, Farmer? player)
    {
        return new Dictionary<string, object?>
        {
            ["monsters"] = this.GetMonsters(location, player).Select(m => new Dictionary<string, object?>
            {
                ["name"] = m.Name,
                ["type"] = m.Type,
                ["tile"] = new[] { (int)m.Tile.X, (int)m.Tile.Y },
                ["health"] = m.Health,
                ["maxHealth"] = m.MaxHealth,
                ["distance"] = Math.Round(m.Distance, 2),
                ["danger"] = m.Distance <= 3 ? "near" : "far",
            }).ToList(),
            ["npcs"] = (object)(location?.characters
                .Where(npc => npc is not Monster)
                .Select(npc => new Dictionary<string, object?>
                {
                    ["name"] = npc.Name,
                    ["tile"] = new[] { (int)npc.Tile.X, (int)npc.Tile.Y },
                    ["distance"] = player != null ? Math.Round(Vector2.Distance(player.Tile, npc.Tile), 2) : 0,
                    ["canTalk"] = npc is NPC,
                    ["isVillager"] = npc is NPC,
                }).Cast<object>().ToList() ?? new List<object>()),
            ["farmAnimals"] = new List<object>(),
        };
    }

    private IEnumerable<MonsterInfo> GetMonsters(GameLocation? location, Farmer? player)
    {
        if (location == null)
            yield break;

        foreach (var monster in location.characters.OfType<Monster>())
        {
            yield return new MonsterInfo
            {
                Name = monster.Name,
                Type = monster.GetType().Name,
                Tile = monster.Tile,
                Health = monster.Health,
                MaxHealth = monster.MaxHealth,
                Distance = player != null ? Vector2.Distance(player.Tile, monster.Tile) : 0,
            };
        }
    }

    private string ClassifyObject(StardewValley.Object obj)
    {
        if (obj.Name.Contains("Stone", StringComparison.OrdinalIgnoreCase))
            return "stone";
        if (obj.Name.Contains("Ore", StringComparison.OrdinalIgnoreCase))
            return "ore";
        if (obj.IsSpawnedObject)
            return "forage";
        if (obj.bigCraftable.Value)
            return "machine";
        if (obj.Name.Contains("Chest", StringComparison.OrdinalIgnoreCase))
            return "chest";
        return "object";
    }

    private Vector2 GetFrontTile(Vector2 tile, int facing)
    {
        return facing switch
        {
            Game1.up => tile + new Vector2(0, -1),
            Game1.right => tile + new Vector2(1, 0),
            Game1.down => tile + new Vector2(0, 1),
            Game1.left => tile + new Vector2(-1, 0),
            _ => tile,
        };
    }

    private string DirectionName(int facing)
    {
        return facing switch
        {
            Game1.up => "up",
            Game1.right => "right",
            Game1.down => "down",
            Game1.left => "left",
            _ => "unknown",
        };
    }

    private string GetWeatherName()
    {
        if (Game1.isRaining)
            return "rain";
        if (Game1.isLightning)
            return "storm";
        if (Game1.isSnowing)
            return "snow";
        if (Game1.isDebrisWeather)
            return "wind";
        return "sunny";
    }

    private sealed class MonsterInfo
    {
        public string Name { get; set; } = "";
        public string Type { get; set; } = "";
        public Vector2 Tile { get; set; }
        public int Health { get; set; }
        public int MaxHealth { get; set; }
        public float Distance { get; set; }
    }

    private sealed class ExportWriteResult
    {
        public ExportWriteResult(Dictionary<string, object?> metrics)
        {
            this.Metrics = metrics;
        }

        public Dictionary<string, object?> Metrics { get; }
    }

    private sealed class WriteAtomicMetrics
    {
        public WriteAtomicMetrics(double cleanupMs, double tempWriteMs, double replaceMs, double totalMs)
        {
            this.CleanupMs = cleanupMs;
            this.TempWriteMs = tempWriteMs;
            this.ReplaceMs = replaceMs;
            this.TotalMs = totalMs;
        }

        public double CleanupMs { get; }
        public double TempWriteMs { get; }
        public double ReplaceMs { get; }
        public double TotalMs { get; }
    }

    private sealed class LightWriteRequest
    {
        public LightWriteRequest(
            string path,
            Dictionary<string, object?> snapshot,
            string signature,
            string reason,
            uint exportSeq,
            uint exportTick,
            double buildSnapshotMs,
            double gameThreadTotalMs,
            string writeReason,
            bool heartbeatDue,
            int heartbeatMs)
        {
            this.Path = path;
            this.Snapshot = snapshot;
            this.Signature = signature;
            this.Reason = reason;
            this.ExportSeq = exportSeq;
            this.ExportTick = exportTick;
            this.BuildSnapshotMs = buildSnapshotMs;
            this.GameThreadTotalMs = gameThreadTotalMs;
            this.WriteReason = writeReason;
            this.HeartbeatDue = heartbeatDue;
            this.HeartbeatMs = heartbeatMs;
        }

        public string Path { get; }
        public Dictionary<string, object?> Snapshot { get; }
        public string Signature { get; }
        public string Reason { get; }
        public uint ExportSeq { get; }
        public uint ExportTick { get; }
        public double BuildSnapshotMs { get; }
        public double GameThreadTotalMs { get; }
        public string WriteReason { get; }
        public bool HeartbeatDue { get; }
        public int HeartbeatMs { get; }
    }
}
