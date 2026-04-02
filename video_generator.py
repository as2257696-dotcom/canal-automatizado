from flask import Flask, request, jsonify
import edge_tts
import asyncio
import requests
import os
import random
from moviepy.editor import *
from moviepy.video.tools.subtitles import SubtitlesClip
import dropbox
import tempfile
import textwrap

app = Flask(__name__)

# ============================================================
# 👇 CAMBIA SOLO ESTAS 3 LÍNEAS
PEXELS_API_KEY = "TU_PEXELS_API_KEY"        # 👈 CAMBIA ESTO
DROPBOX_TOKEN  = "TU_DROPBOX_TOKEN"          # 👈 CAMBIA ESTO
VOICE          = "es-MX-DaliaNeural"         # 👈 Puedes cambiar la voz
# ============================================================

# --- Voces disponibles en español ---
# es-MX-DaliaNeural      (mujer, México)
# es-MX-JorgeNeural      (hombre, México)
# es-ES-ElviraNeural     (mujer, España)
# es-ES-AlvaroNeural     (hombre, España)
# es-AR-ElenaNeural      (mujer, Argentina)


async def generar_audio(texto, archivo_salida):
    """Convierte texto a voz con Edge TTS"""
    tts = edge_tts.Communicate(texto, VOICE)
    await tts.save(archivo_salida)


def buscar_videos_pexels(tema, cantidad=5):
    """Busca videos relacionados al tema en Pexels"""
    headers = {"Authorization": PEXELS_API_KEY}
    params  = {"query": tema, "per_page": cantidad, "orientation": "portrait"}
    res     = requests.get("https://api.pexels.com/videos/search", headers=headers, params=params)
    videos  = []

    if res.status_code == 200:
        for video in res.json().get("videos", []):
            for file in video["video_files"]:
                if file["quality"] == "hd" and file["width"] == 1080:
                    videos.append(file["link"])
                    break
    return videos


def descargar_video(url, ruta):
    """Descarga un video desde una URL"""
    r = requests.get(url, stream=True)
    with open(ruta, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            f.write(chunk)


def crear_clip_subtitulo(txt, duracion, ancho=1080):
    """Crea un clip de texto estilo subtítulo"""
    lineas = textwrap.fill(txt, width=30)
    return (TextClip(lineas,
                     fontsize=55,
                     color="white",
                     font="Arial-Bold",
                     stroke_color="black",
                     stroke_width=2,
                     method="caption",
                     size=(ancho - 80, None))
            .set_duration(duracion)
            .set_position(("center", 0.75), relative=True))


def subir_a_dropbox(ruta_local, nombre_archivo):
    """Sube el video a Dropbox y devuelve el link"""
    dbx  = dropbox.Dropbox(DROPBOX_TOKEN)
    ruta = f"/Videos Canal/{nombre_archivo}"

    with open(ruta_local, "rb") as f:
        dbx.files_upload(f.read(), ruta, mute=True)

    link = dbx.sharing_create_shared_link_with_settings(ruta)
    # Convertir a link de descarga directa
    return link.url.replace("?dl=0", "?dl=1")


@app.route("/generar", methods=["POST"])
def generar_video():
    """
    Endpoint principal. Recibe JSON con:
    {
        "guion": "Texto del guión...",
        "tema":  "naturaleza"
    }
    """
    data  = request.json
    guion = data.get("guion", "")
    tema  = data.get("tema", "naturaleza")

    if not guion:
        return jsonify({"error": "No se recibió guión"}), 400

    with tempfile.TemporaryDirectory() as tmpdir:

        # 1. Generar audio
        audio_path = os.path.join(tmpdir, "audio.mp3")
        asyncio.run(generar_audio(guion, audio_path))
        audio_clip  = AudioFileClip(audio_path)
        duracion    = audio_clip.duration

        # 2. Buscar y descargar videos de Pexels
        urls_videos = buscar_videos_pexels(tema, cantidad=6)
        if not urls_videos:
            urls_videos = buscar_videos_pexels("naturaleza", cantidad=6)

        clips_video = []
        tiempo_acum = 0

        for i, url in enumerate(urls_videos):
            if tiempo_acum >= duracion:
                break
            ruta = os.path.join(tmpdir, f"clip_{i}.mp4")
            descargar_video(url, ruta)
            clip = VideoFileClip(ruta).resize((1080, 1920))

            # Duración proporcional
            seg = min(clip.duration, duracion - tiempo_acum)
            clips_video.append(clip.subclip(0, seg))
            tiempo_acum += seg

        # 3. Concatenar clips de video
        video_base = concatenate_videoclips(clips_video, method="compose")
        video_base = video_base.subclip(0, duracion)

        # 4. Agregar subtítulos automáticos
        # Dividir guión en bloques de ~8 palabras
        palabras     = guion.split()
        palabras_por_bloque = 8
        bloques      = [palabras[i:i+palabras_por_bloque]
                        for i in range(0, len(palabras), palabras_por_bloque)]
        tiempo_bloque = duracion / len(bloques)

        clips_texto = []
        for i, bloque in enumerate(bloques):
            texto = " ".join(bloque)
            clip_txt = crear_clip_subtitulo(texto, tiempo_bloque)
            clip_txt = clip_txt.set_start(i * tiempo_bloque)
            clips_texto.append(clip_txt)

        # 5. Componer video final
        video_final = CompositeVideoClip([video_base] + clips_texto)
        video_final = video_final.set_audio(audio_clip)

        # 6. Exportar
        nombre     = f"video_{random.randint(1000,9999)}.mp4"
        video_path = os.path.join(tmpdir, nombre)
        video_final.write_videofile(video_path,
                                    fps=30,
                                    codec="libx264",
                                    audio_codec="aac",
                                    threads=4,
                                    logger=None)

        # 7. Subir a Dropbox
        link_descarga = subir_a_dropbox(video_path, nombre)

    return jsonify({
        "status":  "ok",
        "video":   link_descarga,
        "nombre":  nombre
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "activo"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
