# Nana Presence Node B6 - So do han day cuoi

Last updated: 2026-08-12 (owner-hold reconciliation; physical evidence through 2026-08-05)

> **OWNER HOLD:** `ESP32-OWNER-HOLD-1` is `WAITING_OWNER` / `DEFERRED`.
> This wiring document remains a reference only. Do not perform any physical
> assembly, soldering, continuity check, voltage measurement, power change,
> flash, or combined-load test until Ba explicitly reopens Presence and gives
> a fresh confirmation. The diagram and prior evidence are preserved.

Status: `PARTIAL PHYSICAL PASS - WAITING FOR REPLACEMENT DISPLAY`. Ngay
2026-08-05, node da han day pass vong test nguon pin on ap, camera, mic, loa,
audio gate, STT va Nana reply. Tam ST7789 bi hong nhiet trong luc lap rap; B6
chua dong cho den khi man thay the duoc lap va continuity, dien ap, boot-mute,
display, cung combined-load smoke deu PASS.

Nguon chan chinh xac: `<NANA_REPO>\nana_presence_node\main\nana_board_pins.h`.

So do nen trang:

- `NANA_B6_WIRING_DIAGRAM.png` - mo nhanh tren PC/dien thoai.
- `NANA_B6_WIRING_DIAGRAM.svg` - phong to khong vo net khi soi chan han.

## Checkpoint vat ly 2026-08-05

- `PASS`: node khoi dong va ket noi Presence Session tren nguon pin da on ap.
- `PASS`: camera, INMP441, MAX98357, audio gate, STT va voice reply theo xac
  nhan truc tiep cua chu so huu; log khong co loi runtime truoc `Ctrl+C`.
- `PASS`: cac doan khong co loi noi on dinh bi audio gate loai truoc STT.
- `BLOCKED`: tam ST7789 hien tai bi chay vang do nhiet khi han/kho; thao khoi
  mach va cach dien cac dau day cho den khi man giong het ve.
- Ket qua display Session/animation truoc day van la bang chung phan mem, nhung
  khong thay the smoke vat ly cho tam man moi.
- Khong thay doi GPIO hay firmware trong luc cho man; khi lap man moi, dau dung
  bang chan ST7789 trong tai lieu nay va chay lai display + combined smoke.

## Khoa an toan

- Rut USB, adapter 5 V va pin/BMS truoc khi doi hoac han bat ky day nao.
- Node chi nhan duong `5 V` da on ap va da do dung cuc: adapter `5 V / 5 A`
  hoac dau ra 5 V tu bo ha ap cua he pin. Tuyet doi khong dua truc tiep pin
  2S2P/BMS `7.4-8.4 V`, bo sac, hoac MB102 vao `5Vin`.
- Khong cam USB va duong 5 V ngoai cung luc cho den khi da xac minh cach ly
  nguon. USB chi dung flash/log khi duong 5 V ngoai da rut.
- INMP441 va ST7789 dung `3V3`, khong dung 5 V.
- Loa chi noi vao `SPK+` va `SPK-`; khong noi bat ky dau loa nao vao GND.
- Thao the microSD. GPIO38/39/40 dang thuoc man hinh trong Presence V1.

## Cay nguon

```text
Adapter +5 V
    |
  cau chi nhanh 3 A
    |
  cong tac 6 A
    |
  nut +5V_SW (Wago)
    +-----------------> ESP32 terminal `5Vin`
    +-----------------> MAX98357 `VIN`

Adapter 0 V/GND
    |
  nut GND_STAR (Wago)
    +-----------------> ESP32 `GND`
    +-----------------> MAX98357 `GND`
    +-----------------> INMP441 `GND`
    +-----------------> ST7789 `GND`

ESP32 `3V3`
    |
  nut 3V3_STAR (Wago)
    +-----------------> INMP441 `VCC/VDD`
    +-----------------> ST7789 `VCC`
    +-----------------> ST7789 `BLK`
```

Day chinh adapter -> cau chi -> cong tac -> Wago: `16 AWG` do/den. Cac nhanh
den board va module, cung toan bo day tin hieu: `22 AWG`. MAX98357 phai co
nhanh 5 V va GND rieng ve hai nut sao; khong cho dong loa chay xuyen qua day
nguon/GND cua ESP32.

## Cac nut noi chung

| Ten nut | Cac diem cung noi |
|---|---|
| `+5V_SW` | Dau ra cong tac, ESP32 `5Vin`, MAX98357 `VIN` |
| `GND_STAR` | Adapter am, ESP32 GND, MAX98357 GND, INMP441 GND, ST7789 GND |
| `3V3_STAR` | ESP32 3V3, INMP441 VCC/VDD, ST7789 VCC, ST7789 BLK |
| `BCLK_41` | ESP32 GPIO41, INMP441 SCK, MAX98357 BCLK |
| `WS_42` | ESP32 GPIO42, INMP441 WS, MAX98357 LRC |

Khong ep hai day vao chung mot oc. Dung mot Wago/splice ba nhanh cho
`BCLK_41` va mot Wago/splice ba nhanh khac cho `WS_42`.

## INMP441

| INMP441 | ESP32 / nut |
|---|---|
| `VCC` hoac `VDD` | `3V3_STAR` |
| `GND` | `GND_STAR` |
| `SCK` | `BCLK_41` |
| `WS` | `WS_42` |
| `SD` | GPIO21 |
| `L/R` | GND |

## MAX98357 va loa

| MAX98357 | ESP32 / nut |
|---|---|
| `VIN` | `+5V_SW` |
| `GND` | `GND_STAR` |
| `BCLK` | `BCLK_41` |
| `LRC` | `WS_42` |
| `DIN` | GPIO47 |
| `SD` | GPIO2 va dien tro keo xuong 10 kOhm |
| `GAIN` | De trong |
| `SPK+` | Day do cua loa |
| `SPK-` | Day den cua loa |

Dien tro 10 kOhm khong nam noi tiep tren day GPIO2. No mac song song nhu sau:

```text
ESP32 GPIO2 --------+-------- MAX98357 SD
                    |
                  10 kOhm
                    |
                   GND
```

Gan sat MAX98357, mac tu hoa `1500 uF / 16 V` song song giua nguon:

```text
MAX98357 VIN  ---- (+ 1500 uF -) ---- MAX98357 GND
```

Chan co vach am cua tu -> GND; chan duong -> VIN. Neu tu gom la ma `104`
(`100 nF`), mac no song song VIN-GND ngay sat module; tu gom khong phan cuc.
Neu ma tu gom khac, dung lai va xac minh truoc khi han.

## ST7789 1.69 inch

| ST7789 | ESP32 / nut |
|---|---|
| `GND` | `GND_STAR` |
| `VCC` | `3V3_STAR` |
| `SCL` | GPIO39 |
| `SDA` | GPIO38 |
| `RES` | GPIO1 |
| `DC` | GPIO40 |
| `CS` | GPIO14 |
| `BLK` | `3V3_STAR` |

`SDA` o day la SPI MOSI, khong phai bus I2C. Camera OV5640 da nam tren board,
khong han them day camera. GPIO48 la LED trang thai tren board, khong noi ngoai.

## Thu tu lap

1. Han day vao cac module va cac moi chia; de ESP32 rut roi khoi terminal adapter.
2. Dau terminal xanh bang dong tran xoan chat hoac ferrule; khong nhung thiếc
   day mem roi kep duoi oc vi thiếc co the creep va long theo thoi gian.
3. Do adapter qua jack: xac nhan dung `+5.0 V` va dung cuc truoc cau chi.
4. Bat cong tac khi chua lap ESP32/module; do `+5V_SW` voi `GND_STAR`.
5. Tat nguon. Do thong mach tung net va kiem tra khong chap 5V-GND,
   3V3-GND, GPIO-GND/5V.
6. Lap module va ESP32; cap chi mot nguon. Boot dau tien phai im loa.
7. Sau khi boot/power PASS moi chay combined camera -> mic -> Core ->
   speaker/display smoke. B6 chi dong sau khi khong brownout, khong nong bat
   thuong, khong hum/re va GPIO2 hard-mute truoc/trong reset.

## Di day vat ly

- Xoan cap `SPK+`/`SPK-`; tach no khoi mic va cac day GPIO21/41/42/47.
- Giữ day I2S ngan; neu co the, di moi day tin hieu canh mot day GND.
- Dat MAX98357 va tu 1500 uF gan nhau, nhung de loa cach mic de giam feedback.
- Co dinh strain relief sau khi test, khong de moi han chiu luc keo cua day.
