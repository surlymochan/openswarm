"""Offline probes of the installed Pi SDK's model registry behavior."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))


class PiSdkRegistryTests(unittest.TestCase):
    def test_duplicate_custom_models_are_collapsed_before_public_lookup(self):
        pi = shutil.which("pi")
        node = shutil.which("node")
        if not pi or not node:
            self.skipTest("installed Pi and Node are required")
        package = os.path.dirname(os.path.dirname(os.path.realpath(pi)))
        entry = os.path.join(package, "dist", "index.js")
        if not os.path.isfile(entry):
            self.skipTest("Pi SDK entry point is unavailable")
        with open(os.path.join(package, "package.json")) as f:
            version = json.load(f).get("version")
        if version not in ("0.73.0", "0.73.1"):
            self.skipTest("unsupported Pi SDK version")
        with tempfile.TemporaryDirectory(prefix=".pi-sdk-test-", dir=ROOT) as tmp:
            registry = os.path.join(tmp, "models.json")
            with open(registry, "w") as f:
                json.dump({"providers": {"synthetic": {
                    "baseUrl": "http://127.0.0.1:1", "apiKey": "synthetic-only",
                    "api": "openai-completions", "models": [
                        {"id": "exact", "name": "first"},
                        {"id": "exact", "name": "second"},
                        {"id": "exact-longer", "name": "partial"},
                    ]}}}, f)
            script = r'''
import { pathToFileURL } from "node:url";
const { AuthStorage, ModelRegistry } = await import(pathToFileURL(process.argv[1]).href);
const storage = AuthStorage.inMemory();
storage.setRuntimeApiKey("synthetic", "synthetic-only");
const registry = ModelRegistry.create(storage, process.argv[2]);
if (registry.getError()) process.exit(2);
const matches = registry.getAll().filter(m => m.provider === "synthetic" && m.id === "exact");
const auth = await registry.getApiKeyAndHeaders(matches[0]);
console.log(JSON.stringify({count: matches.length, name: matches[0]?.name,
  exactMissing: registry.find("synthetic", "exa") === undefined,
  modelContainsKey: JSON.stringify(matches[0]).includes("synthetic-only"),
  authResolved: auth.ok && auth.apiKey === "synthetic-only"}));
'''
            result = subprocess.run([node, "--input-type=module", "-e", script, entry, registry],
                                    capture_output=True, text=True, timeout=20, check=True)
        self.assertEqual(json.loads(result.stdout), {
            "count": 1, "name": "second", "exactMissing": True,
            "modelContainsKey": False, "authResolved": True,
        })
