from __future__ import annotations
import json
from xml.sax.saxutils import escape
from .models import SubtitleSegment

def _timestamp(seconds: float, comma: bool = True) -> str:
    seconds = max(0.0, seconds)
    millis = round(seconds * 1000)
    h, rem = divmod(millis, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{',' if comma else '.'}{ms:03d}"

class SubtitleFormatter:
    """Stateless exporters; each format is independently replaceable."""
    def export(self, segments: list[SubtitleSegment], fmt: str) -> str:
        fmt = fmt.lower().lstrip(".")
        if fmt == "srt": return self._srt(segments)
        if fmt == "vtt": return self._vtt(segments)
        if fmt == "ass": return self._ass(segments)
        if fmt == "ttml": return self._ttml(segments)
        if fmt == "json": return json.dumps([s.model_dump(mode="json") for s in segments], ensure_ascii=False, indent=2)
        raise ValueError(f"Unsupported subtitle format: {fmt}")

    def _srt(self, ss):
        return "\n\n".join(f"{i}\n{_timestamp(s.start_time)} --> {_timestamp(s.end_time)}\n{s.text}" for i,s in enumerate(ss,1)) + ("\n" if ss else "")
    def _vtt(self, ss):
        body = "\n\n".join(f"{_timestamp(s.start_time,False)} --> {_timestamp(s.end_time,False)}\n{s.text}" for s in ss)
        return "WEBVTT\n\n" + body + ("\n" if body else "")
    def _ass(self, ss):
        lines = ["[Script Info]","ScriptType: v4.00+","[V4+ Styles]","Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Alignment","Style: Default,Arial,42,&H00FFFFFF,&H00000000,&H00000000,0,2","[Events]","Format: Layer, Start, End, Style, Text"]
        for s in ss:
            def ass_time(x):
                h=int(x//3600); m=int((x%3600)//60); sec=x%60
                return f"{h}:{m:02d}:{sec:05.2f}"
            clean_text = s.text.replace('\n', r'\N')
            lines.append(
                f"Dialogue: 0,{ass_time(s.start_time)},{ass_time(s.end_time)},Default,{clean_text}"
            )
        return "\n".join(lines)+"\n"
    def _ttml(self, ss):
        body="".join(f'<p begin="{s.start_time:.3f}s" end="{s.end_time:.3f}s">{escape(s.text)}</p>' for s in ss)
        return f'<tt xmlns="http://www.w3.org/ns/ttml"><body><div>{body}</div></body></tt>'
