# FoodStock Reels — Workflow

> Stack: **HyperFrames** + per-clip generator
> Output: 1080×1920 · 30fps · MP4 ต่อคลิป สำหรับ IG Reels / YouTube Shorts
> คนละ project กับ `foodstock-video` (loop TV) — template ใหม่หมด แชร์แค่ brand

## โครง

```
foodstock-reels/
├── design.md            # brand + layout rules (source of truth)
├── WORKFLOW.md          # ไฟล์นี้
├── template/            # HyperFrames template (Codex implement)
├── clips/
│   └── <ชื่อร้าน>/
│       ├── source.mp4       # คลิปดิบแนวตั้ง
│       ├── transcript.json  # จาก whisper — review ก่อน render เสมอ
│       ├── clip.json        # { "restaurant": "...", ... } metadata ต่อคลิป
│       └── index.html       # generated — ห้ามแก้มือ
├── fonts/               # Kanit-Bold/SemiBold, NotoSansThai (จาก foodstock-video)
├── brand/               # logo-darkgreen.png
└── out/                 # <ชื่อร้าน>-vN-YYYY-MM-DD.mp4
```

## Source of Truth: Google Drive

ไฟล์ video ไม่เก็บถาวรบนเครื่อง render — [[Google Drive]] เก็บทุกอย่าง, เครื่องดึงมาทีละคลิปผ่าน rclone

```
Drive: FoodStockReels/inbox/<ชื่อร้าน>/   # source.mp4 + clip.json (+ transcript.json ที่ review แล้ว)
Drive: FoodStockReels/out/               # mp4 ที่ render เสร็จ
Local: clips/<ชื่อร้าน>/                  # staging ชั่วคราว — ลบหลัง render เสร็จ
```

Batch: `./run_batch.sh` (Codex implement) — pull → transcribe/render → push → clean, disk ค้างแค่คลิปเดียว
⚠️ คลิปที่ยังไม่มี transcript ที่ review แล้ว จะหยุดรอ ไม่ auto-render

## Pipeline ต่อคลิป (manual — ทำมือทีละคลิปก็ได้)

```bash
cd Output/foodstock-reels

# 1. วางคลิปดิบ
mkdir -p "clips/เนื้อในตำนาน" && cp ~/clip.mp4 "clips/เนื้อในตำนาน/source.mp4"

# 2. ถอดเสียง → transcript.json
npx hyperframes transcribe "clips/เนื้อในตำนาน/source.mp4"

# 3. ⚠️ REVIEW transcript.json — ชื่อเมนู/ชื่อร้านภาษาไทยมักถอดผิด แก้มือก่อนเสมอ

# 4. เขียน clip.json (ชื่อร้าน + metadata)

# 5. generate index.html ต่อคลิป (inline transcript — HyperFrames ห้าม fetch async)
#    <คำสั่ง generator — Codex จะกำหนดตอน implement>

# 6. render
npx hyperframes render "clips/เนื้อในตำนาน" --output "out/เนื้อในตำนาน-v1-$(date +%Y-%m-%d).mp4"
```

## กฎ

- **Review transcript ก่อน render ทุกครั้ง** — Whisper ถอดชื่อเมนูไทยผิดบ่อย
- แก้ template → แก้ที่ `template/` แล้ว regenerate ทุกคลิป — ห้ามแก้ `index.html` ที่ generate แล้ว
- Source video ควรถ่ายแนวตั้ง 9:16 มาเลย — ถ้าแนวนอนจะโดน crop กลางจอ (object-fit: cover)
- ไฟล์ output ตั้งชื่อ `<ร้าน>-vN-YYYY-MM-DD.mp4` (convention เดียวกับ foodstock-video)

## Status

- 2026-07-10 — scaffold โครง + design.md + brief ส่ง [[Codex]]
- 2026-07-10 — [[Codex]] implement เสร็จ + verified: `template/reel.html`, `make_clip.py`, `run_batch.sh`, sample render ผ่าน (1080×1920·30fps·aac)
- 2026-07-10 — rclone `gdrive:` auth แล้ว + [[Codex]] แก้ remote-mode glob bug (`rclone lsf`) — **pipeline ครบวงจร verified กับ [[Google Drive]] จริง**: inbox → render → out + idempotent ✅ เหลือแค่คลิปจริงจากร้าน
- ⚠️ rclone shared client_id จะถูก Google ปิดช่วงปี 2026 — สร้าง client_id ตัวเองก่อนสิ้นปี ([คู่มือ](https://rclone.org/drive/#making-your-own-client-id))
