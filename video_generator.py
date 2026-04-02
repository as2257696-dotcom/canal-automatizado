from flask import Flask, request, jsonify
import edge_tts
import asyncio
import requests
import os
import random
import textwrap
import dropbox
import tempfile
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydub import AudioSegment

app = Flask(__name__)

# ============================================================
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
DROPBOX_TOKEN  = os.environ.get("DROPBOX_TOKEN", "")
VOICE          = "es-MX-JorgeNeural"
# ============================================================


async def generar_audio(texto, archivo_salida):
    tts = edge_tts.Communicate(texto, VOICE)
    await tts.save(archivo_salida)


def get_duracion_audio(path):
    audio = AudioSegment.from_file(path)
    return len(audio) / 1000.0


def buscar_videos_pexels(tema, cantidad=5):
    headers = {"Authorization": PEXELS_API_KEY}
    params  = {"query": tema, "per_page": cantidad, "orientation": "portrait"}
    res     = requests.get("https://api.pexels.com/videos/search",
                           headers=headers, params=params)
    videos  = []
    if res.status_code == 200:
        for video in res.json().get("videos", []):
            for f in video["video_files"]:
                if f["width"] >= 720:
                    videos.append(f["link"])
                    break
    return videos


def descargar_archivo(url, ruta):
    r = requests.get(url, stream=True)
    with open(ruta, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)


def agregar_subtitulo(frame, texto, ancho, alto):
    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw2 = ImageDraw.Draw(overlay)
    lineas = textwrap.fill(texto, width=28)
    font_size = 52
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        font = ImageFont.load_default()
    bbox = draw2.textbbox((0, 0), lineas, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (ancho - tw) // 2
    y = int(alto * 0.72)
    draw2.rectangle([x - 15, y - 10, x + tw + 15, y + th + 10], fill=(0, 0, 0, 160))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw3 = ImageDraw.Draw(img)
    for dx, dy in [(-2,-2),(2,-2),(-2,2),(2,2)]:
        draw3.text((x+dx, y+dy), lineas, font=font, fill=(0, 0, 0, 255))
    draw3.text((x, y), lineas, font=font, fill=(255, 255, 255, 255))
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def crear_video(clips_paths, audio_path, guion, output_path):
    FPS      = 30
    W, H     = 1080, 1920
    duracion = get_duracion_audio(audio_path)
    out      = cv2.VideoWriter(output_path + "_noaudio.mp4",
                               cv2.VideoWriter_fourcc(*"mp4v"),
                               FPS, (W, H))
    palabras           = guion.split()
    palabras_por_bloque = 7
    bloques            = [" ".join(palabras[i:i+palabras_por_bloque])
                          for i in range(0, len(palabras), palabras_por_bloque)]
    frames_total       = int(duracion * FPS)
    frames_por_bloque  = frames_total // len(bloques)
    clip_index         = 0
    cap                = cv2.VideoCapture(clips_paths[0])

    for bloque in bloques:
        for _ in range(frames_por_bloque):
            ret, frame = cap.read()
            if not ret:
                cap.release()
                clip_index += 1
                cap = cv2.VideoCapture(clips_paths[clip_index % len(clips_paths)])
                ret, frame = cap.read()
                if not ret:
                    frame = np.zeros((H, W, 3), dtype=np.uint8)
            frame = cv2.resize(frame, (W, H))
            frame = agregar_subtitulo(frame, bloque, W, H)
            out.write(frame)

    cap.release()
    out.release()
    os.system(f'ffmpeg -y -i "{output_path}_noaudio.mp4" -i "{audio_path}" '
              f'-c:v copy -c:a aac -shortest "{output_path}"')
    os.remove(output_path + "_noaudio.mp4")


def subir_a_dropbox(ruta_local, nombre_archivo):
    dbx  = dropbox.Dropbox(DROPBOX_TOKEN)
    ruta = f"/Videos Canal/{nombre_archivo}"
    with open(ruta_local, "rb") as f:
        dbx.files_upload(f.read(), ruta,
                         mode=dropbox.files.WriteMode.overwrite,
                         mute=True)
    link = dbx.sharing_create_shared_link_with_settings(ruta)
    return link.url.replace("?dl=0", "?dl=1")


@app.route("/generar", methods=["POST"])
def generar_video():
    data  = request.json
    guion = request.form.get("guion") or (request.json or {}).get("guion", "")
    tema  = request.form.get("tema") or (request.json or {}).get("tema", "Automoviles")
    if not guion:
        return jsonify({"error": "No se recibió guión"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")
        asyncio.run(generar_audio(guion, audio_path))
        urls = buscar_videos_pexels(tema, 5)
        if not urls:
            urls = buscar_videos_pexels("nature", 5)
        clips = []
        for i, url in enumerate(urls):
            p = os.path.join(tmpdir, f"clip_{i}.mp4")
            descargar_archivo(url, p)
            clips.append(p)
        nombre     = f"video_{random.randint(1000,9999)}.mp4"
        video_path = os.path.join(tmpdir, nombre)
        crear_video(clips, audio_path, guion, video_path)
        link = subir_a_dropbox(video_path, nombre)

    return jsonify({"status": "ok", "video": link, "nombre": nombre})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "activo"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
