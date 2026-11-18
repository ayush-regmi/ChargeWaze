/**
 * Entry point for the Mapbox/AFDC provider stub (`node e2e/support/provider-stub.mjs`).
 * How the stub works and how to add a fixture: `provider-stub-lib.mjs`.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

import { startProviderStub } from "./provider-stub-lib.mjs";

startProviderStub({
  dir: path.join(path.dirname(fileURLToPath(import.meta.url)), "provider-fixtures"),
});
