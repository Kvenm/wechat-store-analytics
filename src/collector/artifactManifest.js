import crypto from 'node:crypto';
import { createReadStream } from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';

export const SOURCE_KINDS = Object.freeze({
  EXPORT_FILE: 'export_file',
  OCR_FILE: 'ocr_file',
  CHART_IMAGE: 'chart_image'
});

export const DEFAULT_ARTIFACT_MANIFEST_FILENAME = 'artifacts-manifest.json';

export async function buildCollectedArtifact({
  task,
  shop,
  exportType,
  tableHint = null,
  savedPath,
  originalFilename = null,
  status = 'completed',
  error = null,
  createdAt = new Date().toISOString(),
  metadata = {}
}) {
  const fileStats = await statArtifactFile(savedPath);
  const sha256 = fileStats.exists ? await sha256File(savedPath) : null;

  return {
    source_kind: SOURCE_KINDS.EXPORT_FILE,
    source_type: SOURCE_KINDS.EXPORT_FILE,
    export_type: exportType,
    table_hint: tableHint,
    shop_id: shop?.id ?? null,
    shop_name: shop?.name ?? null,
    date_range: {
      from: task.from,
      to: task.to
    },
    saved_path: savedPath,
    original_filename: originalFilename,
    metadata,
    sha256,
    size_bytes: fileStats.exists ? fileStats.size : null,
    status,
    error,
    created_at: createdAt
  };
}

export async function writeArtifactManifest({
  task,
  artifacts,
  manifestPath = path.join(task.download_dir, DEFAULT_ARTIFACT_MANIFEST_FILENAME)
}) {
  await fs.mkdir(path.dirname(manifestPath), { recursive: true });

  const manifest = {
    task_id: task.task_id,
    source_kinds: Object.values(SOURCE_KINDS),
    generated_at: new Date().toISOString(),
    artifact_count: artifacts.length,
    artifacts
  };

  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2), 'utf8');
  return { ...manifest, manifest_path: manifestPath };
}

async function statArtifactFile(filePath) {
  try {
    const stats = await fs.stat(filePath);
    return {
      exists: stats.isFile(),
      size: stats.size
    };
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return {
        exists: false,
        size: null
      };
    }
    throw error;
  }
}

async function sha256File(filePath) {
  const hash = crypto.createHash('sha256');

  for await (const chunk of createReadStream(filePath)) {
    hash.update(chunk);
  }

  return hash.digest('hex');
}
