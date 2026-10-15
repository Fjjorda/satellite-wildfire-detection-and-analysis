import PDFDocument from 'pdfkit/js/pdfkit.standalone.js';
import type { PdfPayload } from './exports';

self.onmessage = (event: MessageEvent<PdfPayload>) => {
  const data = event.data;
  try {
    const doc = new PDFDocument({
      size: 'A4', margin: 48, tagged: true, pdfVersion: '1.7', displayTitle: true,
      lang: data.language, info: { Title: data.title, Author: 'FireSentinel', Subject: data.description },
    });
    const chunks: Uint8Array[] = [];
    doc.on('data', (chunk: Uint8Array) => chunks.push(chunk));
    doc.on('error', (error: Error) => self.postMessage({ error: error.message }));
    doc.on('end', () => {
      const combined = new Uint8Array(chunks.reduce((total, chunk) => total + chunk.length, 0));
      let offset = 0;
      for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.length; }
      self.postMessage({ buffer: combined.buffer }, { transfer: [combined.buffer] });
    });
    doc.registerFont('Body', data.regularFont);
    doc.registerFont('Bold', data.boldFont);
    doc.font('Body').fontSize(10).fillColor('#182820');
    const root = doc.struct('Document');
    doc.addStructure(root);

    function text(tag: string, content: string, size = 10, bold = false) {
      root.add(doc.struct(tag, {}, () => {
        doc.font(bold ? 'Bold' : 'Body').fontSize(size).text(content + ' ', { paragraphGap: 8 });
      }));
    }
    text('H1', data.title, 22, true);
    text('P', data.generated, 9);
    text('H2', data.descriptionTitle, 14, true);
    text('P', data.description);
    text('H2', data.metricsTitle, 14, true);
    for (const metric of data.metrics) text('P', metric);
    text('H2', data.imageTitle, 14, true);
    const imageHeight = 240;
    if (doc.y + imageHeight + 50 > doc.page.height - 48) doc.addPage();
    const y = doc.y;
    const figureOptions = { alt: data.imageAlt, bbox: [48, y, 547, y + imageHeight] };
    root.add(doc.struct('Figure', figureOptions, () => {
      doc.image(data.image, 48, y, { fit: [499, imageHeight], align: 'center', valign: 'center' });
    }));
    doc.y = y + imageHeight + 12;
    text('Caption', data.layerCaption, 9);
    if (data.notes.length) {
      text('H2', data.notesTitle, 14, true);
      for (const note of data.notes) text('P', note);
    }
    text('H2', data.hotspotsTitle, 14, true);
    if (!data.hotspots.length) text('P', data.noHotspots);
    else {
      const list = doc.struct('L'); root.add(list);
      for (const hotspot of data.hotspots) {
        list.add(doc.struct('LI', {}, [doc.struct('LBody', {}, [doc.struct('P', {}, () => {
          doc.font('Body').fontSize(10).text(hotspot + ' ', { paragraphGap: 10 });
        })])]));
      }
    }
    text('P', data.footer, 9);
    root.end(); doc.end();
  } catch (error) { self.postMessage({ error: error instanceof Error ? error.message : 'pdf_failed' }); }
};
