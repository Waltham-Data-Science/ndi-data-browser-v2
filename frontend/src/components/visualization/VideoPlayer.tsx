import type { VideoData } from '@/api/binary';

interface VideoPlayerProps {
  data: VideoData;
}

export function VideoPlayer({ data }: VideoPlayerProps) {
  if (data.error) {
    const lower = String(data.error).toLowerCase();
    const friendly =
      lower.includes('no download') || lower.includes('download')
        ? 'Video preview is not available for this document. The data file may not be accessible from the cloud.'
        : lower.includes('no file uid') || lower.includes('no file')
          ? 'This document does not have an associated video file.'
          : String(data.error);
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
        {friendly}
      </div>
    );
  }

  if (!data.url) {
    return (
      <div className="text-sm text-slate-500 dark:text-slate-400 p-3">
        No video URL available
      </div>
    );
  }

  return (
    <div className="rounded-md border border-slate-200 dark:border-slate-700 bg-black overflow-hidden">
      <video src={data.url} controls preload="metadata" className="w-full max-h-[500px]">
        Your browser does not support video playback.
      </video>
    </div>
  );
}
