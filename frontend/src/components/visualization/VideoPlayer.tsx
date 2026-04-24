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
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
        {friendly}
      </div>
    );
  }

  if (!data.url) {
    return (
      <div className="text-sm text-gray-500 p-3">
        No video URL available
      </div>
    );
  }

  return (
    <div className="rounded-md border border-gray-200 bg-black overflow-hidden">
      <video src={data.url} controls preload="metadata" className="w-full max-h-[calc(100vh-200px)]">
        Your browser does not support video playback.
      </video>
    </div>
  );
}
