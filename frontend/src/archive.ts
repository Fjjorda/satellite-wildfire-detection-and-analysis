import type { AsyncZippable } from 'fflate';

/**
 * Compresses a complete resource set without blocking the UI during compression.
 * @throws If compression fails or the operation is aborted.
 */
export async function createArchive(files: Record<string, Blob>, signal: AbortSignal): Promise<Blob> {
  const { zip } = await import('fflate');
  const entries: AsyncZippable = {};
  for (const [name, blob] of Object.entries(files)) {
    signal.throwIfAborted();
    const bytes = new Uint8Array(await blob.arrayBuffer());
    entries[name] = [bytes, { level: /\.(png|jpe?g)$/i.test(name) ? 0 : 3 }];
  }
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const cancel = () => {
      terminate();
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const terminate = zip(entries, (error, data) => {
      signal.removeEventListener('abort', cancel);
      if (error) reject(error);
      else resolve(new Blob([new Uint8Array(data)], { type: 'application/zip' }));
    });
    signal.addEventListener('abort', cancel, { once: true });
  });
}
