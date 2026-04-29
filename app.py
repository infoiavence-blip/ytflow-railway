from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp
import os
import tempfile

app = Flask(__name__)
CORS(app)

@app.route('/')
def index():
    return jsonify({ 'status': 'ok', 'service': 'ytflow-audio' })

# ── Info (metadata only) ──────────────────────────────
@app.route('/info')
def info():
    video_id = request.args.get('id', '').strip()
    if not video_id:
        return jsonify({ 'error': 'Missing id' }), 400

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False
            )
        thumb = data.get('thumbnail') or f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'
        return jsonify({
            'id':       video_id,
            'title':    data.get('title', ''),
            'channel':  data.get('uploader') or data.get('channel', ''),
            'duration': int(data.get('duration') or 0),
            'thumb':    thumb,
        })
    except yt_dlp.utils.DownloadError as e:
        return jsonify({ 'error': str(e) }), 400
    except Exception as e:
        return jsonify({ 'error': 'Internal error: ' + str(e) }), 500


# ── Audio download + stream ───────────────────────────
@app.route('/audio')
def audio():
    video_id = request.args.get('id', '').strip()
    if not video_id:
        return jsonify({ 'error': 'Missing id' }), 400

    # Use mkstemp for safety (mktemp is deprecated)
    fd, tmp_base = tempfile.mkstemp()
    os.close(fd)
    os.unlink(tmp_base)  # yt-dlp will create its own file with extension

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[acodec=aac]/bestaudio/best',
        'outtmpl': tmp_base + '.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'm4a',
            'preferredquality': '128',  # 128k is fine for mobile, smaller file
        }],
        # Prevent yt-dlp from downloading age-restricted content etc.
        'age_limit': None,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_data = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=True
            )

        # Find the output file
        actual = None
        for ext in ('m4a', 'mp4', 'webm', 'opus', 'ogg', 'mp3'):
            candidate = f'{tmp_base}.{ext}'
            if os.path.exists(candidate):
                actual = candidate
                break

        # Sometimes yt-dlp names it differently after postprocessing
        if not actual:
            base_dir = os.path.dirname(tmp_base)
            base_name = os.path.basename(tmp_base)
            for fname in os.listdir(base_dir):
                if fname.startswith(base_name):
                    actual = os.path.join(base_dir, fname)
                    break

        if not actual or not os.path.exists(actual):
            return jsonify({ 'error': 'Audio file not found after processing' }), 500

        file_ext  = actual.rsplit('.', 1)[-1].lower()
        mime_map  = {
            'm4a':  'audio/mp4',
            'mp3':  'audio/mpeg',
            'opus': 'audio/ogg; codecs=opus',
            'ogg':  'audio/ogg',
            'webm': 'audio/webm',
            'mp4':  'audio/mp4',
        }
        mime      = mime_map.get(file_ext, 'audio/mp4')
        file_size = os.path.getsize(actual)

        title    = info_data.get('title') or video_id
        channel  = info_data.get('uploader') or info_data.get('channel', '')
        duration = int(info_data.get('duration') or 0)
        thumb    = info_data.get('thumbnail') or f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'

        def safe_ascii(s):
            """Encode header value safely for HTTP headers."""
            return str(s).encode('ascii', errors='replace').decode('ascii')

        def stream_file():
            try:
                with open(actual, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                try:
                    os.remove(actual)
                except OSError:
                    pass

        headers = {
            'Content-Length':                str(file_size),
            'Content-Type':                  mime,
            'Content-Disposition':           'inline',
            'Access-Control-Allow-Origin':   '*',
            'Access-Control-Expose-Headers': 'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
            'X-Title':    safe_ascii(title),
            'X-Channel':  safe_ascii(channel),
            'X-Duration': str(duration),
            'X-Thumb':    safe_ascii(thumb),
            'X-Ext':      file_ext,
        }

        return Response(
            stream_with_context(stream_file()),
            status=200,
            headers=headers,
            mimetype=mime,
            direct_passthrough=True,
        )

    except yt_dlp.utils.DownloadError as e:
        # Clean up temp files on error
        for ext in ('m4a', 'mp4', 'webm', 'opus', 'ogg', 'mp3', ''):
            try:
                p = tmp_base + ('.' + ext if ext else ext)
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return jsonify({ 'error': str(e) }), 400

    except Exception as e:
        return jsonify({ 'error': 'Internal error: ' + str(e) }), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, threaded=True)
