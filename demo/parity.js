// Prints the engine's answers for every day of both sample experiments as JSON on stdout.
const E = require("./engine.js");
const data = require("./data.json");
const QUESTIONS = ["what did kids think of the pulley toy", "which channel brought families", "was there an injury",
  "can we share photos", "what did buyers say about the price", "Instagram or Google", "zzz nothing here"];
const out = {};
for (const exp of data.experiments) {
  const days = [];
  for (let d = exp.start; d <= exp.end; d = E.addDays(d, 1)) days.push(d);
  out[exp.id] = {
    days: days.map((day) => {
      const recs = E.visibleAt(exp.records, day);
      const results = E.evaluate(exp.charter, recs, day);
      const concl = E.conclusionReport(exp.charter, recs, day);
      return { day, results, decision: E.decide(exp.charter, results, day),
        review: E.weeklyReview(exp.charter, recs, day), report: concl.report, evidence: concl.evidence };
    }),
    documents: exp.records.map((r) => E.toDocument(r)),
    search: QUESTIONS.map((q) => E.searchRecords(exp.records, q, 10).map((s) => [s.hits, s.record.id])),
  };
}
process.stdout.write(JSON.stringify(out));
