# Universal Video Downloader

MVP full-stack downloader untuk URL publik TikTok, Instagram, dan YouTube.

## Yang dibutuhkan

- Python 3.11+
- FFmpeg
- Browser modern

## 1. Install dependency

Masuk ke folder project:

```bash
python -m venv .venv
```

Windows:
```bash
.venv\Scripts\activate
```

Linux/macOS:
```bash
source .venv/bin/activate
```

Lalu:

```bash
pip install -r backend/requirements.txt
```

Install FFmpeg dan pastikan command `ffmpeg` tersedia di PATH.

## 2. Jalankan backend

```bash
uvicorn backend.app.main:app --reload
```

Backend:
http://127.0.0.1:8000

## 3. Jalankan frontend

Untuk testing cepat, dari folder project:

```bash
python -m http.server 5500 --directory frontend
```

Buka:

http://127.0.0.1:5500

## 4. Docker

```bash
copy .env.example .env
docker compose up --build
```

Linux/macOS:

```bash
cp .env.example .env
docker compose up --build
```

## Catatan kualitas

Aplikasi tidak bisa menciptakan detail 4K jika sumber hanya menyediakan 720p/1080p.
Pilihan kualitas berasal dari format yang dapat diekstrak dari sumber.

## Catatan platform

Platform dapat mengubah sistem mereka sewaktu-waktu. Extractor juga perlu diperbarui.
Konten privat, DRM, login-bypass, paywall-bypass, CAPTCHA-bypass, dan security circumvention
tidak didukung.

## Catatan produksi

MVP ini sengaja sederhana. Untuk deployment publik, tambahkan:
- reverse proxy HTTPS
- persistent job queue (mis. Redis/Celery/RQ)
- object storage
- authenticated admin/metrics
- strict CORS
- stronger SSRF controls
- per-IP rate limiting di reverse proxy
- cleanup worker
- process cancellation supervision
