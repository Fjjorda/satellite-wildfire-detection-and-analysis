import type { z } from 'zod';
import type { Composition, UploadedImage, uploadSchema } from './domain';

const filenamePatterns = [
  ['swir', /\bswir\b/],
  ['truecolor', /\btrue\s*colou?r\b/],
  ['wildfire', /\bwildfires?\b/],
] as const satisfies ReadonlyArray<readonly [Composition, RegExp]>;

/**
 * Prioritizes an explicit composition name over the visual suggestion.
 * Ambiguous or unnamed files retain the server's suggestion for manual review.
 */
export function assignUploadedImage(upload: z.infer<typeof uploadSchema>): UploadedImage {
  const name = upload.name.replace(/\.[^.]+$/, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ');
  const matches = filenamePatterns.filter(([, pattern]) => pattern.test(name));
  const namedType = matches.length === 1 ? matches[0]?.[0] : undefined;
  return {
    ...upload,
    assignment: namedType ?? upload.detected,
    assignmentSource: namedType ? 'filename' : 'visual',
  };
}
