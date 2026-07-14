# FoodStock Reels — Design

Vertical social clips (IG Reels / YouTube Shorts) ของร้านใน [[FoodStock]].
Brand เดียวกับ foodstock-video (loop TV) — palette/typography ยกมาตรง แต่โครงคนละแบบ:
เดิม = 1 composition 38 scenes / อันนี้ = **1 template + render ต่อคลิป** (video เต็มจอ + overlay น้อยๆ)

## Canvas

- **1080×1920 (9:16) · 30fps · MP4**
- ความยาว = ความยาว source video ต่อคลิป (ไม่ fix)

## Palette (จาก foodstock-video/design.md)

| Token | Hex | Use |
|-------|-----|-----|
| slate-900 | `#2A333B` | title card bg, subtitle pill bg (opacity .78) |
| slate-700 | `#495862` | โทน logo |
| cream | `#F5EFE6` | primary text บน slate |
| cream-dim | `#C7CDD2` | secondary text |
| amber | `#E0A43B` | accent — ขีดใต้ชื่อร้าน, karaoke highlight ใน subtitle |
| amber-bright | `#F2BB52` | glow/emphasis |

## Typography

- **ชื่อร้าน (title card):** `Kanit` 700 — 96–128px (fitTextFontSize ถ้ายาว)
- **Subtitle:** `Noto Sans Thai` 600 — 52–58px, cream บน slate pill, max 2 บรรทัด
- Fonts local: `fonts/Kanit-Bold.ttf`, `fonts/Kanit-SemiBold.ttf`, `fonts/NotoSansThai.ttf`

## Safe Areas (IG Reels / Shorts UI)

- **บน:** เว้น 220px (username/camera)
- **ล่าง:** เว้น 320px (caption/CTA ของ platform)
- **ขวา:** เว้น 180px (like/comment/share rail)
- Subtitle วางกึ่งกลางแนวนอน, baseline อยู่ ~`bottom: 360px`
- Logo overlay: มุม**ซ้ายบน** `top: 240px; left: 48px` กว้าง ~150px (หลบ UI ทั้งสองฝั่ง)

## Structure ต่อคลิป

1. **Title card** (0–1.8s, slate-900 bg) — logo FoodStock เล็กบน + ชื่อร้าน Kanit ใหญ่ + ขีด amber 4px + "FoodStock · พัฒนาการ 32" (cream-dim)
2. **Video เต็มจอ** (object-fit: cover) — เริ่มซ้อนใต้ title card แล้ว crossfade 0.6s เข้า
   - Logo overlay ซ้ายบนตลอดคลิป (opacity .92)
   - Subtitle ตามเสียงพูดตลอด (จาก transcript ที่ review แล้ว)
3. **จบ** — fade to slate-900 0.5s + logo กลางจอ 1.2s (outro สั้นมาก ไม่ยืด)

## Motion

- Calm เหมือน loop เดิม — crossfade, up-fade, ไม่มี effect ฉูดฉาด
- Title card: ชื่อร้าน up-fade `power3.out`, ขีด amber scaleX `power2.out`, logo fade `sine.out`
- Subtitle: fade+y 12px ต่อ cue, **cue เก่าออกก่อน cue ใหม่เข้าเสมอ**
- ห้าม ken-burns / transform บน video (video มี motion ในตัวแล้ว)

## Audio

- เสียงจาก source video — `<video muted>` + `<audio>` แยก ชี้ไฟล์เดียวกัน (กฎ HyperFrames)
- ไม่ใส่เพลงประกอบใน template (ใส่ตอนโพสต์บน platform ได้)

## Don'ts

- ห้ามบีบ/ยืด video (object-fit: cover เท่านั้น)
- ห้ามวาง text/logo นอก safe area
- Subtitle ห้ามเกิน 2 บรรทัด — ถ้าเกิน ตัด cue ให้สั้นลง
- ห้าม full-screen linear gradient บน slate (H.264 banding)
- ไม่มี pure black — slate-900 คือสีเข้มสุด
