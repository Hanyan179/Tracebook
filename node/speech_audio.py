"""Read the recorder's PCM CAF without changing its original file.

This deliberately supports only our signed 16-bit mono 16 kHz capture contract.
Other CAF codecs fail visibly rather than uploading mislabeled or partial audio.
"""
import array
import io
import struct
import sys
import wave


class AudioError(ValueError):
    pass


def pcm_layout(path):
    size = path.stat().st_size
    with path.open('rb') as source:
        if source.read(8) != b'caff\x00\x01\x00\x00':
            raise AudioError('原音格式不受支持，原文件已保留。')
        description = None
        audio = None
        while source.tell() < size:
            header = source.read(12)
            if len(header) != 12:
                raise AudioError('原音文件不完整，尚未发送。')
            kind, length = struct.unpack('>4sq', header)
            start = source.tell()
            if kind == b'data' and length == -1:
                length = size - start
            if length < 0 or start + length > size:
                raise AudioError('原音文件不完整，尚未发送。')
            if kind == b'desc':
                if description is not None or length != 32:
                    raise AudioError('原音格式不受支持，原文件已保留。')
                description = struct.unpack('>d4sIIIII', source.read(32))
            elif kind == b'data':
                if audio is not None or length < 6:
                    raise AudioError('原音文件不完整，尚未发送。')
                audio = (start + 4, length - 4)
            source.seek(start + length)
        if (description is None or audio is None or
                description[:2] != (16000.0, b'lpcm') or
                description[2] not in (0, 2) or description[3:] != (2, 1, 1, 16) or audio[1] % 2):
            raise AudioError('原音格式不受支持，原文件已保留。')
        return {'offset': audio[0], 'frames': audio[1] // 2, 'little': description[2] == 2}


def chunks(path, seconds=600):
    """At most 19.2 MB per part; prefer a quiet boundary in the last five seconds.

    Boundaries cover every sample exactly once. Chunk text is stored individually,
    so retrying does not resend completed parts. Silence detection is not diarization.
    """
    layout = pcm_layout(path)
    start = 0
    with path.open('rb') as source:
        while start < layout['frames']:
            end = min(start + int(seconds * 16000), layout['frames'])
            if end < layout['frames'] and end - start > 80000:
                source.seek(layout['offset'] + (end - 80000) * 2)
                samples = array.array('h', source.read(160000))
                if layout['little'] != (sys.byteorder == 'little'):
                    samples.byteswap()
                for i in range(len(samples) - 1600, -1, -1600):
                    if sum(v*v for v in samples[i:i+1600]) / 1600 < 90000:
                        end = end - 80000 + i + 1600
                        break
            yield layout, start, end
            start = end


def wav_part(path, layout, start, end):
    with path.open('rb') as source:
        source.seek(layout['offset'] + start * 2)
        raw = source.read((end - start) * 2)
    if len(raw) != (end - start) * 2:
        raise AudioError('原音文件不完整，尚未发送。')
    if not layout['little']:
        samples = array.array('h', raw)
        samples.byteswap()
        raw = samples.tobytes()
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(raw)
    return output.getvalue()
