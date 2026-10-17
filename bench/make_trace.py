"""Build bench/trace.jsonl from the fixed rubric. Run once; the file is checked in."""
import json
import os

from rubric import label_task

# (id, type, difficulty, title, optional context override)
SPECS = [
    ("t01", "impl", 1, "Add a null guard before the config lookup in src/load.ts"),
    ("t02", "impl", 1, "Return 400 when the name query parameter is empty"),
    ("t03", "impl", 2, "Thread a request id through the four call sites in api/client.ts"),
    ("t04", "impl", 2, "Add an optional timeout argument to retry() and plumb it through"),
    ("t05", "impl", 2, "Surface the parser's line number on SyntaxError"),
    ("t06", "impl", 3, "Implement cursor pagination on the list-invoices endpoint"),
    ("t07", "impl", 3, "Add idempotency keys to the charge creation path"),
    ("t08", "impl", 3, "Support dry-run on the migrate command without writing files"),
    ("t09", "impl", 3, "Persist the feature flag snapshot beside the user record"),
    ("t10", "impl", 4, "Split the billing worker so tax and invoice steps can fail independently"),
    ("t11", "impl", 4, "Add a compatibility layer so v1 webhook payloads keep their old field names"),
    ("t12", "impl", 4, "Introduce a repository interface and move SQL out of the order service"),
    ("t13", "impl", 5, "Replace the in-process queue with a lease-based worker that survives restarts"),
    ("t14", "impl", 5, "Design the migration from session cookies to rotating refresh tokens"),
    ("t15", "refactor", 2, "Extract the duplicated date formatting into one helper"),
    ("t16", "refactor", 2, "Collapse the three identical error wrappers in cli/"),
    ("t17", "refactor", 3, "Separate parsing from validation in the config loader"),
    ("t18", "refactor", 3, "Move the email templates out of the send function into their own module"),
    ("t19", "refactor", 3, "Replace boolean flag arguments on export() with a small options object"),
    ("t20", "refactor", 4, "Untangle the import cycle between scheduler, jobs, and metrics"),
    ("t21", "refactor", 4, "Rework the plugin loader so each plugin owns its own subcommand tree"),
    ("t22", "refactor", 5, "Restructure the storage layer so reads and writes no longer share a connection"),
    ("t23", "refactor", 5, "Split the monolith's auth package into token, session, and directory modules"),
    ("t24", "mechanical", 1, "Rename the internal helper _fmt to formatLabel across src/ui"),
    ("t25", "mechanical", 1, "Apply the project's import order to the files touched yesterday"),
    ("t26", "mechanical", 1, "Convert the remaining function() callbacks in lib/http.js to arrows"),
    ("t27", "mechanical", 1, "Add the missing trailing commas the formatter would insert"),
    ("t28", "mechanical", 2, "Update every call of getUser(id) to getUser({id}) after the signature change"),
    ("t29", "mechanical", 2, "Replace deprecated assert.equal with assert.strictEqual in the unit tests"),
    ("t30", "mechanical", 2, "Move the hardcoded port 8080 to the existing PORT constant"),
    ("t31", "rename", 1, "Rename the package util/str to util/text and fix imports"),
    ("t32", "rename", 1, "Rename the CLI flag --dryrun to --dry-run everywhere it is documented in code"),
    ("t33", "rename", 1, "Rename Customer.fullName to Customer.displayName and update the serializer"),
    ("t34", "format", 1, "Run the repo formatter on pkg/report and keep the diff to whitespace"),
    ("t35", "format", 1, "Normalize line endings and indentation in scripts/legacy.sh"),
    ("t36", "docs", 1, "Fix the broken example in the README quickstart"),
    ("t37", "docs", 1, "Document the three environment variables the server actually reads"),
    ("t38", "docs", 2, "Add a short docstring to each public function in pkg/retry"),
    ("t39", "docs", 3, "Write the operator notes for rotating the signing key"),
    ("t40", "docs", 3, "Describe the on-disk layout of the job queue for the runbook"),
    ("t41", "docs", 5, "Write the architecture note for the new lease-based worker"),
    ("t42", "docs", 5, "Draft the migration guide from session cookies to refresh tokens"),
    ("t43", "test", 1, "Add a unit test for the empty-name rejection"),
    ("t44", "test", 1, "Cover the null-config branch in src/load.ts"),
    ("t45", "test", 2, "Add table tests for the retry timeout argument"),
    ("t46", "test", 2, "Assert the parser error includes the source line number"),
    ("t47", "test", 3, "Add an integration test for cursor pagination across three pages"),
    ("t48", "test", 3, "Test that a repeated idempotency key does not double-charge"),
    ("t49", "test", 3, "Exercise the dry-run migrate path against a fixture repo"),
    ("t50", "test", 4, "Add a failure-injection test for the tax step of the billing worker"),
    ("t51", "review", 2, "Review the null-guard patch for missed call sites"),
    ("t52", "review", 2, "Review the import-order-only diff before it merges"),
    ("t53", "review", 3, "Review the pagination change for off-by-one and empty-page behavior"),
    ("t54", "review", 3, "Review the idempotency-key patch against double-submit races"),
    ("t55", "review", 3, "Review the repository extraction for queries that were left behind"),
    ("t56", "review", 3, "Review the webhook compatibility layer for fields v1 clients still send"),
    ("t57", "review", 4, "Review the plugin-loader rework for changes in command help and exit codes"),
    ("t58", "review", 4, "Review the import-cycle fix for behavior changes in metric registration"),
    ("t59", "audit", 3, "Audit the charge path for places a failed write still returns success"),
    ("t60", "audit", 3, "Audit token storage for secrets written to logs or error strings"),
    ("t61", "audit", 5, "Audit the refresh-token design for replay and theft scenarios"),
    ("t62", "audit", 5, "Audit the lease worker for lock loss and double execution"),
    ("t63", "debug", 2, "The CLI prints a stack trace when PORT is unset; it should print a one-line error"),
    ("t64", "debug", 2, "A unit test fails only when run after the date-helper test"),
    ("t65", "debug", 3, "Pagination returns the same cursor twice on the last page"),
    ("t66", "debug", 3, "Dry-run migrate still creates the backup directory"),
    ("t67", "debug", 4, "The billing worker marks an invoice paid when the tax call times out"),
    ("t68", "debug", 4, "Plugin subcommands shadow the built-in help command after the loader change"),
    ("t69", "debug", 5, "Workers occasionally run the same leased job on two hosts"),
    ("t70", "debug", 5, "Refresh tokens stay valid after the password changes"),
    ("t71", "architecture", 5, "Choose how the job lease should interact with deploys and crashes"),
    ("t72", "architecture", 5, "Decide whether the billing worker should share a database transaction with tax"),
    ("t73", "architecture", 5, "Pick the boundary between the token issuer and the session store"),
    ("t74", "architecture", 5, "Lay out how plugins should declare commands without owning process globals"),
    ("t75", "research", 2, "Read the legacy report scripts and list every file they write", 280000),
    ("t76", "research", 2, "Read the generated API client and note which methods ignore errors", 280000),
    ("t77", "research", 3, "Read the billing package and trace who updates invoice status", 280000),
    ("t78", "research", 3, "Read the scheduler and list every job that takes a database lock", 640000),
    ("t79", "research", 3, "Read the auth package end to end and name the token lifetimes", 640000),
    ("t80", "research", 5, "Read the storage and worker packages and explain the lease lifecycle", 640000),
]


def build():
    rows = []
    for item in SPECS:
        spec = {"id": item[0], "type": item[1], "difficulty": item[2], "title": item[3]}
        if len(item) > 4:
            spec["context_tokens"] = item[4]
        rows.append(label_task(spec))
    return rows


def main():
    rows = build()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trace.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} tasks to {path}")


if __name__ == "__main__":
    main()
