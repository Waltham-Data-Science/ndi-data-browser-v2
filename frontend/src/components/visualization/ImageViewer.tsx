import { useState } from 'react';
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from 'lucide-react';

import type { ImageData } from '@/api/binary';
import { Button } from '@/components/ui/Button';

interface ImageViewerProps {
  data: ImageData;
  /** Called when the user picks a different frame on a multi-frame image
   * stack. The caller is responsible for re-fetching the image for that
   * frame if needed. */
  onFrameChange?: (frame: number) => void;
}

/** Scientific image viewer with frame stepper + zoom — ported from v1.
 * Zoom is CSS-only so browsers can handle the full-fidelity image bytes
 * without re-downloading. Frame stepper fires onFrameChange so the parent
 * can drive the backend with `?frame=N` once supported. */
export function ImageViewer({ data, onFrameChange }: ImageViewerProps) {
  const [zoom, setZoom] = useState(1);
  const [currentFrame, setCurrentFrame] = useState(0);

  if (data.error) {
    const lower = String(data.error).toLowerCase();
    const friendly =
      lower.includes('no download') || lower.includes('download')
        ? 'Image preview is not available for this document. The data file may not be accessible from the cloud.'
        : lower.includes('no file uid') || lower.includes('no file')
          ? 'This document does not have an associated image file.'
          : String(data.error);
    return (
      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
        {friendly}
      </div>
    );
  }

  if (!data.dataUri) {
    return (
      <div className="text-sm text-gray-500 dark:text-gray-400 p-3">
        No image data available
      </div>
    );
  }

  const nFrames = data.nFrames ?? 1;
  const isStack = nFrames > 1;

  const handleFrameChange = (f: number) => {
    const clamped = Math.max(0, Math.min(nFrames - 1, f));
    setCurrentFrame(clamped);
    onFrameChange?.(clamped);
  };

  return (
    <div className="space-y-3">
      {/* Info bar */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 font-mono">
          <span>
            {data.width} × {data.height}
          </span>
          {data.mode && <span>{data.mode}</span>}
          {isStack && <span>{nFrames} frames</span>}
          {data.format && <span>{data.format}</span>}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="secondary"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
            aria-label="Zoom out"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </Button>
          <span className="text-xs text-gray-500 dark:text-gray-400 font-mono w-12 text-center">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
            aria-label="Zoom in"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <div className="rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-black/40 overflow-auto max-h-[calc(100vh-200px)] min-h-[320px] flex items-center justify-center p-2">
        <img
          src={data.dataUri}
          alt="NDI image data"
          style={{ transform: `scale(${zoom})`, transformOrigin: 'center' }}
          className="transition-transform"
        />
      </div>

      {isStack && (
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => handleFrameChange(currentFrame - 1)}
            disabled={currentFrame === 0}
            aria-label="Previous frame"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <input
            type="range"
            min={0}
            max={nFrames - 1}
            value={currentFrame}
            onChange={(e) => handleFrameChange(Number(e.target.value))}
            className="flex-1"
            aria-label={`Frame ${currentFrame + 1} of ${nFrames}`}
          />
          <Button
            variant="secondary"
            size="sm"
            className="h-7 w-7 p-0"
            onClick={() => handleFrameChange(currentFrame + 1)}
            disabled={currentFrame === nFrames - 1}
            aria-label="Next frame"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
          <span className="text-xs text-gray-500 dark:text-gray-400 font-mono w-20 text-center">
            {currentFrame + 1} / {nFrames}
          </span>
        </div>
      )}
    </div>
  );
}
