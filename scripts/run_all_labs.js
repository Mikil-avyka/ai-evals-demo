/**
 * Run every lab in order, even when a lab has failing tests.
 *
 * Why a script instead of `npm run lab1 && npm run lab2 ...`?
 * promptfoo exits with code 100 whenever any test fails — and in this demo the
 * "buggy" columns are SUPPOSED to fail. Chaining with && would stop after Lab 1.
 * This runner keeps going and prints a one-line summary per lab at the end.
 *
 * Usage:  npm run all-labs
 */
const { spawnSync } = require("node:child_process");

const LABS = [
  "lab1_retrieval",
  "lab2_faithfulness",
  "lab3_tool_calling",
  "lab4_security",
  "lab5_multi_turn",
  "lab6_model_comparison",
  "lab7_judge_calibration",
];

const summary = [];
for (const lab of LABS) {
  console.log(`\n========== ${lab} ==========`);
  const started = Date.now();
  const result = spawnSync("npx", ["promptfoo", "eval", "-c", `evals/${lab}/promptfooconfig.yaml`], {
    stdio: "inherit",
    shell: true, // needed on Windows to resolve npx.cmd
  });
  const seconds = Math.round((Date.now() - started) / 1000);
  // 0 = all passed, 100 = some tests failed (expected for buggy builds), anything else = a real error
  const status = result.status === 0 ? "all passed" : result.status === 100 ? "some tests failed" : `ERROR (exit ${result.status})`;
  summary.push(`${lab.padEnd(24)} ${status.padEnd(20)} ${seconds}s`);
}

console.log("\n========== Summary ==========");
for (const line of summary) console.log(line);
console.log("\nOpen the results with: npm run view");
