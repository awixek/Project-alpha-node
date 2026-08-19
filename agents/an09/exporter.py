from __future__ import annotations

from .models import ExportMetadata, RenderSettings, VideoProviderResponse


class ExportEngine:
    """Normalizes backend output metadata into the downstream VideoPackage contract."""

    SUPPORTED_FORMATS = frozenset({"mp4", "mov", "mkv", "webm"})

    def export_metadata(self, response: VideoProviderResponse, settings: RenderSettings) -> ExportMetadata:
        if settings.export_format not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported export format: {settings.export_format}")
        return ExportMetadata(
            format=settings.export_format,
            uri=response.video_uri,
            resolution=response.resolution,
            fps=response.fps,
            codec=response.codec,
            bitrate=response.bitrate,
            duration_seconds=response.duration_seconds,
            size_bytes=response.size_bytes,
        )
