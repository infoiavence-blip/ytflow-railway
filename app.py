from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp, os, tempfile, subprocess, sys, threading, time

app = Flask(__name__)
CORS(app, origins='*')

def update_ytdlp():
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-q', '--upgrade', 'yt-dlp'],
            capture_output=True, timeout=120
        )
        print(f"yt-dlp update: {result.returncode}")
    except Exception as e:
        print(f"Update failed: {e}")

threading.Thread(target=update_ytdlp, daemon=True).start()

@app.route('/')
def index():
    import yt_dlp as ytdlp_module
    return jsonify({'status': 'ok', 'yt_dlp': ytdlp_module.version.__version__})

@app.route('/audio')
def audio():
    vid = request.args.get('id', '').strip()
    if not vid:
        return jsonify({'error': 'Missing id'}), 400

    url = f'https://www.youtube.com/watch?v={vid}'
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    os.unlink(tmp)

    formats = [
        'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'bestaudio/best',
        '140/251/250/249/bestaudio/best',
    ]

    clients = [
        ['android', 'web'],
        ['ios', 'web'],
        ['android'],
        ['web'],
    ]

    info = None
    out_file = None

    for client in clients:
        for fmt in formats:
            opts = {
                'quiet': True,
                'no_warnings': True,
                'format': fmt,
                'outtmpl': tmp + '.%(ext)s',
                'retries': 2,
                'fragment_retries': 2,
                'socket_timeout': 20,
                'extractor_args': {'youtube': {'player_client': client}},
                'http_headers': {
                    'User-Agent': 'com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip',
                },
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '128',
                }],
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                for ext in ('mp3', 'm4a', 'mp4', 'webm', 'opus', 'ogg'):
                    c = f'{tmp}.{ext}'
                    if os.path.exists(c) and os.path.getsize(c) > 1000:
                        out_file = c
                        break

                if out_file:
                    print(f"Success: client={client} fmt={fmt} file={out_file}")
                    break
            except Exception as e:
                print(f"Failed client={client} fmt={fmt}: {e}")
                _cleanup(tmp)
                info = None
                out_file = None

        if out_file:
            break

    if not out_file:
        return jsonify({'error': 'No se pudo descargar. El vídeo puede estar restringido.'}), 400

    ext = out_file.rsplit('.', 1)[-1].lower()
    mime = {'mp3':'audio/mpeg','m4a':'audio/mp4','webm':'audio/webm','mp4':'audio/mp4'}.get(ext,'audio/mpeg')
    size = os.path.getsize(out_file)

    def safe(s):
        return str(s or '').encode('utf-8','replace').decode('ascii','replace')

    title    = safe(info.get('title','') if info else vid)
    channel  = safe((info.get('uploader') or info.get('channel','')) if info else '')
    duration = str(int(info.get('duration') or 0) if info else 0)
    thumb    = safe((info.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg') if info else '')

    def stream():
        try:
            with open(out_file, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk: break
                    yield chunk
        finally:
            try: os.remove(out_file)
            except: pass

    return Response(
        stream_with_context(stream()),
        status=200,
        mimetype=mime,
        headers={
            'Content-Length': str(size),
            'Content-Type': mime,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
            'X-Title': title,
            'X-Channel': channel,
            'X-Duration': duration,
            'X-Thumb': thumb,
            'X-Ext': ext,
        }
    )

def _cleanup(tmp):
    for ext in ('mp3','m4a','mp4','webm','opus','ogg'):
        try:
            p = f'{tmp}.{ext}'
            if os.path.exists(p): os.remove(p)
        except: pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, threaded=True)
