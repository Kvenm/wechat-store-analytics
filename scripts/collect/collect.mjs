#!/usr/bin/env node
import { runCollection } from '../../src/collector/collect.js';

try {
  const result = await runCollection(process.argv.slice(2));
  if (result?.check_config && !result.ok) {
    process.exitCode = 1;
  }
} catch (error) {
  console.error(error.message);
  if (process.env.DEBUG && error.stack) {
    console.error(error.stack);
  }
  process.exitCode = 1;
}
