# main.py
import os
import io
import csv
import zipfile
from pathlib import Path
from fastapi import FastAPI, Request
from typing import Optional
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi.templating import Jinja2Templates




MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://ann41422_db_user:12345testtest@cluster0.9l56haf.mongodb.net/")  # 本機測試可以先寫死，Deploy 再改成環境變數
DB_NAME = "emogo_db"

app = FastAPI()


templates = Jinja2Templates(directory="templates")


# 1. 啟動時連線 MongoDB

@app.get("/items/{item_id}")
def read_item(item_id: int, q: Optional[str] = None):
    return {"item_id": item_id, "q": q}

@app.on_event("startup")
async def startup_db_client():
    app.mongodb_client = AsyncIOMotorClient(MONGODB_URI)
    app.mongodb = app.mongodb_client[DB_NAME]

@app.on_event("shutdown")
async def shutdown_db_client():
    app.mongodb_client.close()

# 2. 掛載影片資料夾：/videos/... -> 專案裡的 videos/ 檔案
app.mount("/videos", StaticFiles(directory="videos"), name="videos")

# 3. 簡單 health check
@app.get("/")
async def root():
    return {"message": "EmoGo backend is running"}

# 4. /export：回三種資料的 JSON

@app.get("/export_json")
async def export_data_json(request: Request):
    samples = await app.mongodb["samples_ts_rating_gps"].find().to_list(10000)

    base_url = str(request.base_url).rstrip("/")

    vlogs = []
    for s in samples:
        filename = s.get("video_filename")
        if not filename:
            continue
        video_url = f"{base_url}/videos/{filename}"
        vlogs.append({
            "id": s.get("id"),
            "ts": s.get("ts"),
            "video_filename": filename,
            "video_url": video_url,
        })

    sentiments = [
        {
            "id": s.get("id"),
            "ts": s.get("ts"),
            "mood": s.get("mood"),
        }
        for s in samples
    ]

    gps = [
        {
            "id": s.get("id"),
            "ts": s.get("ts"),
            "lat": s.get("lat"),
            "lng": s.get("lng"),
        }
        for s in samples
    ]

    return {"vlogs": vlogs, "sentiments": sentiments, "gps": gps}


@app.get("/export", response_class=HTMLResponse)
async def export(request: Request):
    # 直接從「一個」 collection 抓所有資料
    docs = (
        await app.mongodb["samples_ts_rating_gps"]  
        .find()
        .sort("ts", 1) 
        .to_list(None)
    )

    rows = []
    for doc in docs:
        # 1) Timestamp 去掉小數點
        ts_raw = doc.get("ts")
        ts_str = str(ts_raw).split(".")[0]  # 變成 "2025-11-26T10:23:35"

        # 2) 經緯度四捨五入
        lat = round(float(doc.get("lat", 0)), 4)
        lng = round(float(doc.get("lng", 0)), 4)

        # 3) 影片檔名用 videoname（照你現在的欄位名）
        filename = doc.get("videoname")
        file_url = f"/videos/{filename}" if filename else None

        rows.append(
            {
                "id": doc.get("id"),      # 或者用 enumerate 給新的 index
                "timestamp": ts_str,
                "mood": doc.get("mood"),
                "lat": lat,
                "lng": lng,
                "filename": filename,
                "file_url": file_url,
            }
        )

    return templates.TemplateResponse(
        "export.html",
        {
            "request": request,
            "rows": rows,
        },
    )



@app.get("/export-zip")
async def export_zip():
    # 讀出所有資料（跟 /export 一樣）
    docs = (
        await app.mongodb["samples_ts_rating_gps"]   # ← 換成你的 collection 名稱
        .find()
        .sort("ts", 1)
        .to_list(None)
    )

    # 建一個記憶體中的位元組緩衝區來放 zip
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # -------- 1) 先建立 CSV 檔內容 --------
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)

        # CSV 欄位（這裡不放 Download/Preview）
        writer.writerow(["ID", "Timestamp", "Mood", "Latitude", "Longitude", "Filename"])

        for doc in docs:
            ts_raw = doc.get("ts")
            ts_str = str(ts_raw).split(".")[0]  # 去掉小數點

            lat = round(float(doc.get("lat", 0)), 4)
            lng = round(float(doc.get("lng", 0)), 4)

            filename = doc.get("videoname")

            writer.writerow([
                doc.get("id"),
                ts_str,
                doc.get("mood"),
                lat,
                lng,
                filename,
            ])

        # 把 CSV 寫入 zip（檔名叫 emogo_export.csv）
        zf.writestr("emogo_export.csv", csv_buffer.getvalue())

        # -------- 2) 再把所有有對應檔案的影片塞進 zip --------
        videos_dir = Path("videos")
        for doc in docs:
            filename = doc.get("videoname")
            if not filename:
                continue

            video_path = videos_dir / filename
            if video_path.exists():
                # 第二個參數是 zip 裡面的路徑，整理一下放在 videos/ 底下
                zf.write(video_path, arcname=f"videos/{filename}")
            # 如果影片檔不存在就略過，避免整包錯掉

    # 準備好 zip 回傳
    zip_buffer.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="emogo_package.zip"'
    }
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers=headers,
    )
