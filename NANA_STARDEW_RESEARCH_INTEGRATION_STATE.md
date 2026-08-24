# Nana Stardew Research Integration State

Last updated: 2026-06-20

## Current Focus

Hiện tại không tiếp tục mở rộng osu. osu Cursor Dance/survival profile đã được
xem là đủ dùng ở thời điểm này và chỉ quay lại khi user yêu cầu rõ.

Trọng tâm hiện tại là Stardew Adapter V2. Nhánh Stardew Python cũ đã retired,
legacy command bands đã bị cắt khỏi `main.py`, và Nana hiện dùng hướng sạch:
Python chỉ observer/planner, còn executor tương lai nằm ở SMAPI/C#.

Mắt đọc game đã nối bước đầu: NanaBridge/SMAPI ghi state thật ra
`<NANA_REPO>/nana/game/stardew/data/observer_state.json`, Python observer đọc lại và
`/stardew-observer-status` đã được user live-verify là fresh.

## User Goal

User muốn Nana trong Stardew hoạt động theo hướng đơn giản hơn:

- Python/Core Nana quyết định goal cấp cao.
- C# SMAPI/NanaBridge xử lý pathfinding, movement, tool/action trong game.
- Python không nên giữ quá nhiều controller nhỏ, phase nhỏ, guard stack rời rạc.
- Nếu có thể, tận dụng cách các bot/mod Stardew mã nguồn mở đã làm sẵn để giảm
  200+ file kỹ thuật.

## Research Repos Downloaded

User đã tải các repo nghiên cứu vào:

```text
D:/bot/research/stardew/
```

Các thư mục hiện có:

```text
D:/bot/research/stardew/stardew-valley-bot-framework-main
D:/bot/research/stardew/stardew-valley-water-bot-main
D:/bot/research/stardew/stardew-mcp-main
D:/bot/research/stardew/Farmtronics-main
```

Các repo này cần được mổ xẻ read-only trước. Không copy code bừa, không lắp vào
Nana ngay.

## Intended Direction

Hướng đang xét:

Nana Python:

- giữ vai trò companion/planner/supervisor
- đọc world state
- chọn goal như `move_to`, `water_crop`, `farm_loop`
- ghi request đơn giản sang bridge

NanaBridge / SMAPI C#:

- xử lý pathfinding
- điều khiển Farmer
- dùng API trong game thay vì giả lập phím quá sâu ở Python
- tự báo trạng thái, lỗi, hoàn thành, kẹt đường

Python supervisor:

- quan sát
- log
- quyết định bước tiếp theo
- không điều khiển từng tile nhỏ nếu C# đã làm được

## Architecture Anchor

Giữ đúng hướng đã khóa:

```text
World Grid Layer
-> Dynamic Overlay
-> Movement Controller v2
-> Travel / Farming / Interaction
```

Nhưng cần xem có thể đẩy phần executor/path/action xuống C# để giảm Python
sprawl hay không.

## What To Inspect Next

Mổ xẻ 4 repo theo thứ tự:

1. `stardew-valley-bot-framework-main`
   - tìm bot loop
   - pathfinding
   - movement/action abstraction
   - callback khi đến đích

2. `stardew-valley-water-bot-main`
   - ví dụ thực tế về tưới cây
   - refill nước
   - chọn tile cây trồng
   - dùng tool đồng bộ với movement

3. `stardew-mcp-main`
   - xem schema/API/LLM bridge
   - học cách expose action cấp cao
   - không bê nguyên nếu quá lớn

4. `Farmtronics-main`
   - học command vocabulary/API abstraction
   - có thể không phù hợp để copy vì scope lớn

## Cleanup Goal

Sau khi đọc xong, cần lập bảng:

Keep:

- file/module Stardew hiện tại vẫn có giá trị

Quarantine:

- phase cũ, duplicate controller, BusStop micro experiments, stale guarded stack

Replace:

- phần Python đang làm việc mà C#/SMAPI có thể làm gọn hơn

Do not delete immediately. Trước tiên chỉ lập kế hoạch và danh sách.

## Safety Rules

- Không chạy Nana live.
- Không chạy game input.
- Không sửa code khi user chưa yêu cầu.
- Chỉ đọc repo và tổng hợp.
- Nếu cần test thì chỉ compile/smoke mock, không live input.
- Giữ observer -> dry-run -> live cho Stardew.
- Live input vẫn phải có env gate và operator token.

## Current Status

**2026-06-20: Stardew Adapter V2 Observer Bridge Healthy**

Old Stardew Python movement stack is retired:

- Legacy Stardew command bands were removed from `main.py`.
- Python has no Stardew keyboard/mouse/pathfinding/micro-pulse executor.
- V2 command surface is active:
  `/stardew-status`, `/stardew-observer-status`, `/stardew-plan-status`,
  `/stardew-bridge-status`, `/stardew-help`.

**V2 Observer/Bridge flow:**

```text
NanaBridge/SMAPI
-> <NANA_REPO>/nana/game/stardew/data/observer_state.json
-> StardewObserver
-> /stardew-observer-status and /stardew-bridge-status
```

User live verification showed fresh real game state:

```text
Available: true
Zone: Farm
Player tile: {'x': 64, 'y': 21}
Energy: 270
Stale: false
```

`/stardew-bridge-status` now reports read-only observer bridge health:

- `Connected` means the observer file is fresh.
- `Executor: read_only_observer`
- `Executor connected: false`
- `Input allowed: false`
- `Can execute commands: false`

**Validation:**

```text
smoke_stardew_adapter_v2_skeleton.py: 31/31
smoke_stardew_adapter_v2_command_surface.py: 62/62
smoke_stardew_adapter_v2_observer.py: 48/48
smoke_stardew_observer_state_writer.py: 28/28
```

**Architecture:** Observer -> Planner -> SMAPI/C# Bridge.

**Next task:** Task 8 should teach the planner to make read-only high-level
decisions from fresh observer state while still outputting safe JSON/noop only.
