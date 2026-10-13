/* Woody engine, JavaScript port of woody/analysis.py, woody/records.py and the offline search in
 * woody/hydra.py (LocalBrain). tests/test_demo.py runs this file and the Python side on the same
 * records for every day of both sample experiments and fails if any number, flag, decision,
 * report line or search ranking differs.
 * Dates are ISO strings (YYYY-MM-DD), which sort the same way as dates. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.WoodyEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var THIN_SAMPLE = 5;
  var OPS = {
    ">=": function (a, b) { return a >= b; },
    ">": function (a, b) { return a > b; },
    "<=": function (a, b) { return a <= b; },
    "<": function (a, b) { return a < b; },
    "==": function (a, b) { return a === b; },
    "!=": function (a, b) { return a !== b; }
  };
  var REL_PHRASES = {
    supports: "supports", contradicts: "contradicts", measured_by: "is measured by",
    for_session: "is for session", by_family: "is by family", produced_by: "was produced by",
    based_on: "is based on", about: "is about", part_of: "is part of"
  };

  /* Python's str() for the values that appear in records. */
  function pyStr(v) {
    if (v === true) return "True";
    if (v === false) return "False";
    if (v === null || v === undefined) return "None";
    return String(v);
  }
  function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(); }

  function rows(metric, records) {
    var where = metric.where || {};
    return records.filter(function (r) {
      if (r.kind !== metric.kind) return false;
      for (var k in where) if (r.fields[k] !== where[k]) return false;
      return true;
    });
  }

  /* [value | null, evidence ids] */
  function measure(metric, records) {
    var rs = rows(metric, records);
    var agg = metric.agg;
    if (agg === "count") return [rs.length, rs.map(function (r) { return r.id; })];
    var used = rs.filter(function (r) { return metric.field in r.fields; });
    if (!used.length) return [null, []];
    var values = used.map(function (r) { return r.fields[metric.field]; });
    var ids = used.map(function (r) { return r.id; });
    if (agg === "sum") return [values.reduce(function (a, b) { return a + b; }, 0), ids];
    if (agg === "mean") return [values.reduce(function (a, b) { return a + b; }, 0) / values.length, ids];
    if (agg === "share") {
      var p = metric.predicate, op = OPS[p.op], hit = 0;
      values.forEach(function (v) { if (op(v, p.value)) hit++; });
      return [hit / values.length, ids];
    }
    throw new Error("unknown aggregation " + agg);
  }

  /* [[group, value, ids], ...] sorted by group name, like Python's sorted(dict.items()) */
  function breakdown(metric, records) {
    var groups = {};
    rows(metric, records).forEach(function (r) {
      var g = (metric.group_by in r.fields) ? pyStr(r.fields[metric.group_by]) : "unknown";
      (groups[g] = groups[g] || []).push(r);
    });
    return Object.keys(groups).sort().map(function (g) {
      var m = measure(metric, groups[g]);
      return [g, m[0], m[1]];
    });
  }

  function evaluate(charter, records, today) {
    return charter.hypotheses.map(function (h) {
      var metric = h.metric;
      var res = { id: h.id, title: h.title, flags: [] };
      if ("group_by" in metric) {
        res.status = "report";
        res.groups = breakdown(metric, records);
        var seen = {};
        res.groups.forEach(function (g) { g[2].forEach(function (i) { seen[i] = 1; }); });
        res.evidence = Object.keys(seen).sort();
        if (!res.groups.length) res.status = "no_data";
        return res;
      }
      var m = measure(metric, records);
      res.value = m[0]; res.evidence = m[1]; res.n = m[1].length;
      res.target = "value" in h ? h.value : null; res.op = h.op || null;
      if (m[0] === null) res.status = "no_data";
      else if ("op" in h) res.status = OPS[h.op](m[0], h.value) ? "pass" : "miss";
      else res.status = "report";
      if (m[0] !== null && (metric.agg === "share" || metric.agg === "mean") && m[1].length < THIN_SAMPLE)
        res.flags.push("thin evidence: only " + m[1].length + " responses");
      if (h.by && res.status !== "pass" && today > h.by) res.flags.push("deadline " + h.by + " has passed");
      return res;
    });
  }

  function decide(charter, results, today) {
    var after = charter.decide_after;
    var fin = !after || today >= after;
    var values = {};
    results.forEach(function (r) { values[r.id] = (r.value === undefined) ? null : r.value; });
    for (var i = 0; i < charter.decision_rules.length; i++) {
      var rule = charter.decision_rules[i], ok = true;
      for (var j = 0; j < rule.all.length; j++) {
        var c = rule.all[j], v = values[c.h];
        if (v === null || v === undefined || !OPS[c.op](v, c.v)) { ok = false; break; }
      }
      if (ok) return { outcome: rule.outcome, because: rule.because || "", rule: rule.all, final: fin };
    }
    return { outcome: charter.default_outcome || "pivot", because: "No go or stop rule matched.", rule: [], final: fin };
  }

  /* Python's format(v, ".0%"): the product is rounded half to even. */
  function pct(v) {
    var x = v * 100, f = Math.floor(x), d = x - f, r;
    if (d < 0.5) r = f; else if (d > 0.5) r = f + 1; else r = (f % 2 === 0) ? f : f + 1;
    return r + "%";
  }
  /* Python's format(v, "g") */
  function fmtG(v) {
    if (Number.isInteger(v)) return String(v);
    return String(Number(v.toPrecision(6)));
  }
  function fmt(v, metric) {
    if (v === null || v === undefined) return "no data";
    return metric.agg === "share" ? pct(v) : fmtG(v);
  }

  function groupText(r, h, withN) {
    return r.groups.map(function (g) {
      return g[0] + (withN ? " " : ": ") + fmt(g[1], h.metric) + " (n=" + g[2].length + ")";
    }).join("; ");
  }

  function weeklyReview(charter, records, today) {
    var results = evaluate(charter, records, today);
    var lines = ["# Weekly review: " + charter.name, ""];
    results.forEach(function (r, i) {
      var h = charter.hypotheses[i];
      var line = "- " + r.id + " " + r.title + ": ";
      if (r.status === "report" && r.groups) line += groupText(r, h, true) || "no data";
      else {
        line += fmt(r.value, h.metric);
        if ("op" in h) line += " against " + h.op + " " + fmt(h.value, h.metric) + " -> " + r.status.toUpperCase();
      }
      if (r.flags.length) line += "  [" + r.flags.join("; ") + "]";
      lines.push(line);
    });
    var d = decide(charter, results, today);
    var label = d.final ? "Current reading" : "Provisional reading (final call after " + charter.decide_after + ")";
    lines.push("", label + ": " + d.outcome.toUpperCase() + (d.because ? " (" + d.because + ")" : ""));
    var weak = results.filter(function (r) { return r.status === "no_data" || r.flags.length; });
    lines.push("");
    lines.push(weak.length
      ? "Decision needed from you: " + weak.map(function (r) { return r.id + " " + r.title + " has weak or missing evidence"; }).join("; ") + "."
      : "Decision needed from you: none; evidence is in for every hypothesis.");
    return lines.join("\n") + "\n";
  }

  function conclusionReport(charter, records, today) {
    var results = evaluate(charter, records, today);
    var decision = decide(charter, results, today);
    var evidence = [];
    var lines = ["# Conclusion: " + charter.name, "", "**Question.** " + charter.question, ""];
    if (!decision.final) lines.push("**Provisional only.** The charter asks for the final call after " + charter.decide_after + ".", "");
    lines.push(("**Decision: " + decision.outcome.toUpperCase() + ".** " + decision.because).replace(/\s+$/, ""));
    lines.push("", "## Findings", "");
    results.forEach(function (r, i) {
      var h = charter.hypotheses[i], ids = r.evidence, body;
      evidence = evidence.concat(ids);
      if (r.groups) body = groupText(r, h, false) || "no data";
      else {
        body = fmt(r.value, h.metric);
        if ("op" in h) body += " vs pass line " + h.op + " " + fmt(h.value, h.metric) + ": " + r.status;
      }
      var flag = r.flags.length ? " Caution: " + r.flags.join("; ") + "." : "";
      lines.push("- **" + r.id + " " + r.title + "**: " + body + "." + flag + " Evidence: " + (ids.length ? ids.join(", ") : "none") + ".");
    });
    var uniq = {}; evidence.forEach(function (e) { uniq[e] = 1; });
    return { report: lines.join("\n") + "\n", decision: decision, evidence: Object.keys(uniq).sort() };
  }

  function toDocument(rec) {
    var K = capitalize(rec.kind);
    var lines = ["# " + K + " " + rec.id, "Experiment: " + rec.experiment_id, "Recorded: " + rec.ts, ""];
    for (var k in rec.fields) lines.push("- " + k + ": " + pyStr(rec.fields[k]));
    if (rec.links.length) {
      lines.push("", "Relationships:");
      rec.links.forEach(function (l) { lines.push("- " + K + " " + rec.id + " " + REL_PHRASES[l.rel] + " " + l.to + "."); });
    }
    return lines.join("\n") + "\n";
  }

  /* Mirror of LocalBrain.ask: count question words (3+ letters) found in each document. */
  function searchRecords(records, question, max) {
    var seen = {}, words = [];
    (question.toLowerCase().match(/[a-z0-9]+/g) || []).forEach(function (w) {
      if (w.length > 2 && !seen[w]) { seen[w] = 1; words.push(w); }
    });
    var scored = [];
    records.forEach(function (rec) {
      var doc = toDocument(rec), low = doc.toLowerCase(), hits = 0;
      words.forEach(function (w) { if (low.indexOf(w) !== -1) hits++; });
      if (hits) scored.push({ hits: hits, doc: doc, record: rec, words: words });
    });
    scored.sort(function (a, b) { return b.hits - a.hits; }); /* stable */
    return scored.slice(0, max === undefined ? 10 : max);
  }

  function visibleAt(records, asof) {
    return records.filter(function (r) { return r.ts.slice(0, 10) <= asof; });
  }

  function addDays(iso, n) {
    var d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }
  function daysBetween(a, b) {
    return Math.round((new Date(b + "T00:00:00Z") - new Date(a + "T00:00:00Z")) / 86400000);
  }

  /* The metric's value at the end of each day in [days], using only records up to that day. */
  function dailySeries(metric, records, days) {
    return days.map(function (day) {
      var m = measure(metric, visibleAt(records, day));
      return { day: day, value: m[0] === null ? 0 : m[0], n: m[1].length };
    });
  }

  return {
    measure: measure, breakdown: breakdown, evaluate: evaluate, decide: decide, fmt: fmt,
    weeklyReview: weeklyReview, conclusionReport: conclusionReport, toDocument: toDocument,
    searchRecords: searchRecords, visibleAt: visibleAt, addDays: addDays, daysBetween: daysBetween,
    dailySeries: dailySeries, pyStr: pyStr, REL_PHRASES: REL_PHRASES
  };
});
