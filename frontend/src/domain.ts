import { z } from 'zod';

export const compositionSchema = z.enum(['swir', 'truecolor', 'wildfire']);
export type Composition = z.infer<typeof compositionSchema>;
export const compositionNames = { swir: 'SWIR', truecolor: 'True Color', wildfire: 'Wildfire' } as const;
export const layerKeys = ['burned', 'under_smoke', 'water', 'smoke', 'smoke_low', 'perimeter', 'front', 'fire'] as const;
export type LayerKey = typeof layerKeys[number];
export type LayerVisibility = Record<LayerKey, boolean>;
export const layerColors: Record<LayerKey, string> = {
  burned: '#D55E00', under_smoke: '#CC79A7', front: '#F0E442', fire: '#E69F00',
  water: '#0072B2', smoke: '#56B4E9', perimeter: '#D55E00', smoke_low: '#333333',
};
export const defaultLayers: LayerVisibility = {
  burned: true, under_smoke: true, water: true, smoke: true, smoke_low: false,
  perimeter: true, front: true, fire: true,
};
export const uploadSchema = z.object({
  id: z.string(), name: z.string(), width: z.number(), height: z.number(), bytes: z.number(),
  detected: compositionSchema, preview: z.string(),
});
export type UploadedImage = z.infer<typeof uploadSchema> & {
  assignment: Composition | 'unused';
  assignmentSource: 'filename' | 'visual' | 'manual';
};
export const MAX_INPUT_IMAGES = 3;
const confidence = z.enum(['alta', 'media', 'baja']);
const hotspotSchema = z.object({
  id: z.number(), centroid_xy: z.tuple([z.number(), z.number()]),
  bbox_xywh: z.tuple([z.number(), z.number(), z.number(), z.number()]),
  n_pixels: z.number(), peak_red: z.number(), mean_ratio: z.number(),
  intensity: confidence, confidence, location: z.enum(['frente', 'interior', 'aislado']),
  near_smoke: z.boolean(), reasons: z.array(z.enum(['edge', 'secondary', 'interior', 'isolated', 'smoke', 'low_contrast', 'truecolor_only', 'other'])),
  contrast: z.number().optional(), reason_details: z.array(z.string()).optional(),
});
export type Hotspot = z.infer<typeof hotspotSchema>;
export const reportSchema = z.object({
  version: z.string(), width: z.number(), height: z.number(), valid_pixels: z.number(), detected: z.boolean(),
  burned_pixels: z.number(), strict_pixels: z.number(), burned_percent: z.number(),
  probable_pixels: z.number(), probable_percent: z.number(), perimeter_pixels: z.number(),
  water_pixels: z.number(), water_inside_pixels: z.number(), islands: z.number(), smoke_pixels: z.number(),
  smoke_low_regions: z.number(), front_sectors: z.number(),
  direction: z.enum(['nw', 'n', 'ne', 'w', 'center', 'e', 'sw', 's', 'se']),
  hotspots: z.array(hotspotSchema), rejected_hotspots: z.array(hotspotSchema),
  notes: z.array(z.enum(['no_truecolor', 'wildfire_experimental', 'smoke_low', 'under_smoke', 'smoke_obscured', 'composition_mismatch', 'reference_only', 'truecolor_only'])),
  validation: z.record(z.string(), z.unknown()).optional(),
});
export type Report = z.infer<typeof reportSchema>;
export const optionsSchema = z.object({
  max_side: z.union([z.literal(1000), z.literal(1600), z.literal(2500)]),
  smoke_recovery: z.boolean(),
});
export type AnalysisOptions = Pick<z.infer<typeof optionsSchema>, 'max_side'>;
const layerSchema = z.object({ file: z.string(), count: z.number() });
export const resultSchema = z.object({
  report: reportSchema,
  bases: z.partialRecord(compositionSchema, z.string()),
  layers: z.record(z.enum(layerKeys), layerSchema), files: z.array(z.string()),
  inputs: z.partialRecord(compositionSchema, uploadSchema), options: optionsSchema,
});
export type AnalysisResult = z.infer<typeof resultSchema>;
export const jobSchema = z.object({
  id: z.string(), status: z.enum(['queued', 'running', 'completed', 'failed']),
  stage: z.string(), error: z.string().nullable(), result: resultSchema.nullable(),
});
export type Job = z.infer<typeof jobSchema>;
export const stages = ['queued', 'loading', 'masks', 'burned', 'fire', 'metrics', 'exports', 'completed'] as const;

export function assignmentsValid(images: UploadedImage[]): boolean {
  const active = images.filter(image => image.assignment !== 'unused');
  return images.length <= MAX_INPUT_IMAGES && active.length === images.length
    && active.some(image => image.assignment === 'swir' || image.assignment === 'truecolor')
    && new Set(active.map(image => image.assignment)).size === active.length;
}
