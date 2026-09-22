from pathlib import Path
from ..errors import InvalidMedia, UnsupportedMedia

def validate_media(path: Path, content_type: str='', expected_size: int | None=None):
    from ..validation import validate_mp3, inspect_error_prefix
    size=path.stat().st_size
    if size==0: raise InvalidMedia('invalid_media: empty audio response')
    if expected_size is not None and size!=expected_size:
        raise InvalidMedia(f'invalid_media: expected {expected_size} bytes, received {size}',retryable=True)
    with path.open('rb') as stream: prefix=stream.read(4096)
    inspect_error_prefix(prefix)
    if prefix.startswith(b'OggS'):
        from .ogg import validate_ogg
        return validate_ogg(path, content_type, expected_size)
    if prefix.startswith(b'ID3') or (len(prefix)>=2 and prefix[0]==255 and prefix[1]&224==224):
        return validate_mp3(path,content_type,expected_size)
    raise UnsupportedMedia('unsupported_media_format: content is not supported MP3 or Ogg audio')
