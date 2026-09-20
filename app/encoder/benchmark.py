"""
Encoder benchmark utility comparing libx264 vs available hardware acceleration encoders.
Measures encoding speed, output file size, CPU utilization, and duration.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import psutil

from app.config import config
from app.utils.formatting import human_bytes
from app.utils.logging import logger


@dataclass
class BenchmarkResult:
    encoder_name: str
    elapsed_seconds: float
    fps: float
    output_size_bytes: int
    speed_factor: float
    avg_cpu_percent: float
    success: bool
    error_message: Optional[str] = None


async def run_encoder_benchmark(
    test_video_path: str | Path,
    test_duration_seconds: int = 15,
    target_quality: str = "720p",
) -> list[BenchmarkResult]:
    """
    Runs a benchmark test across available candidate encoders (libx264, h264_nvenc, h264_amf, h264_qsv, h264_mediacodec).
    """
    in_p = Path(test_video_path).resolve()
    temp_dir = config.storage.temp_dir
    temp_dir.mkdir(parents=True, exist_ok=True)

    candidate_encoders = ["libx264", "h264_nvenc", "h264_amf", "h264_qsv", "h264_mediacodec"]
    results: list[BenchmarkResult] = []

    ffmpeg_bin = config.encoder.ffmpeg_binary

    for enc in candidate_encoders:
        out_file = temp_dir / f"bench_{enc}_{target_quality}.mp4"
        if out_file.exists():
            out_file.unlink(missing_ok=True)

        cmd = [
            ffmpeg_bin,
            "-y",
            "-t", str(test_duration_seconds),
            "-i", str(in_p),
            "-vf", "scale=-2:720",
            "-c:v", enc,
            "-c:a", "aac",
            "-b:a", "128k",
            str(out_file),
        ]

        logger.info(f"Running benchmark on encoder: {enc}...")
        start_time = time.time()
        cpu_samples: list[float] = []

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Sample CPU usage while encoding
            while proc.returncode is None:
                cpu_samples.append(psutil.cpu_percent(interval=0.2))
                if proc.returncode is not None:
                    break
                await asyncio.sleep(0.3)
                if proc.returncode is not None:
                    break

            stdout, stderr = await proc.communicate()
            elapsed = time.time() - start_time
            avg_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0

            if proc.returncode != 0:
                results.append(
                    BenchmarkResult(
                        encoder_name=enc,
                        elapsed_seconds=elapsed,
                        fps=0.0,
                        output_size_bytes=0,
                        speed_factor=0.0,
                        avg_cpu_percent=avg_cpu,
                        success=False,
                        error_message=f"Encoder unsupported or failed (code {proc.returncode})",
                    )
                )
            else:
                out_size = out_file.stat().st_size if out_file.exists() else 0
                speed_factor = test_duration_seconds / elapsed if elapsed > 0 else 0.0
                results.append(
                    BenchmarkResult(
                        encoder_name=enc,
                        elapsed_seconds=round(elapsed, 2),
                        fps=round((test_duration_seconds * 30) / elapsed, 1) if elapsed > 0 else 0.0,
                        output_size_bytes=out_size,
                        speed_factor=round(speed_factor, 2),
                        avg_cpu_percent=round(avg_cpu, 1),
                        success=True,
                    )
                )
        except Exception as e:
            results.append(
                BenchmarkResult(
                    encoder_name=enc,
                    elapsed_seconds=0.0,
                    fps=0.0,
                    output_size_bytes=0,
                    speed_factor=0.0,
                    avg_cpu_percent=0.0,
                    success=False,
                    error_message=str(e),
                )
            )
        finally:
            if out_file.exists():
                out_file.unlink(missing_ok=True)

    return results
