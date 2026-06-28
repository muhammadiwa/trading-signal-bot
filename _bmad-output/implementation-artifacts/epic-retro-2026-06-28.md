# Epic 1 Retrospective — Daily Signal Pipeline

**Date:** 2026-06-28 | **Stories:** 8/8 done | **Tests:** 28

---

## Summary

Epic 1 delivered the complete daily signal pipeline: data fetch → indicators → strategies → profile matching → backtest → signal generation → filter → Telegram delivery → orchestrator. All 8 stories completed, reviewed, and merged.

---

## What Went Well

1. **Pure numpy/pandas indicators** — Keputusan untuk tidak dependensi `pandas-ta` (Python 3.14 compatibility) bekerja sempurna. Semua 14 indikator dihitung dengan benar tanpa external library TA.
2. **Fallback chain CCXT** — Binance→OKX→CoinGecko dengan retry 3x dan stale cache fallback. Robust di semua skenario.
3. **5 strategi dengan strategy protocol** — Momentum Breakout, Trend Following, Mean Reversion, Volatility Breakout, Volume-Price Divergence. Semua pure functions, mudah di-backtest.
4. **Code review adversarial menemukan 33 masalah** — Blind Hunter + Edge Case + Auditor. 27 fix diterapkan, 5 deferred, 6 dismissed.

## What Didn't Go Well

1. **Pipeline wiring di main.py** — Stage interkoneksi awalnya broken: `research_results` dict tidak diinisialisasi (NameError), return value didiscard, CoinGecko cache corruption dengan `volume=0.0`. Baru ketahuan saat code review.
2. **AD-6 docs drift** — Architecture menyebut `pandas-ta` padahal kode pure numpy/pandas. Story 1.1 AC1 import test juga mencantumkan `pandas_ta`. Baru difix di documentation sync.
3. **Telegram delivery** — `send_alert` asyncio broken dari scheduler thread, format tidak sesuai spek AC (multi-line vs single-line summary). Semua difix.

## Key Insights

- **Module-level code selalu robust, wiring di orchestrator yang rapuh.** Semua fungsi di `exchange.py`, `indicators.py`, `strategies/base.py` benar. Bug hampir semuanya di `main.py` — interkoneksi antar stage.
- **Code review wajib 3-layer.** Blind Hunter menemukan bug yang Acceptance Auditor lewatkan, dan sebaliknya.
- **Tests passing belum tentu production-ready.** DB schema discrepancy (`'unresolvable'` missing dari CHECK constraint) hanya terdeteksi karena reviewer baca `db.py` asli.

## Action Items

1. [ ] **Selalu uji pipeline end-to-end** sebelum merge — minimal smoke test `python main.py` dengan data mock.
2. [ ] **Schema changes harus ALTER TABLE** untuk backward compat — `CREATE TABLE IF NOT EXISTS` tidak update existing table.
3. [ ] **Dokumentasi architecture harus sync dengan kode** setelah setiap story selesai.

---

## Epic 2 Retrospective — Research Context Integration

**Date:** 2026-06-28 | **Stories:** 6/6 done | **Tests:** 44

---

## Summary

Epic 2 menambahkan research context: sentiment (Fear & Greed + Reddit), on-chain (Whale Alert + CoinGecko), macro calendar, prediction markets (Polymarket), research multiplier, dan enhanced Telegram formatting.

---

## What Went Well

1. **Research multiplier formula** — `sentiment × onchain × (1-macro) + prediction_adjustment` clamped [0.5,1.5]. Diperbaiki dari PRD yang awalnya kurang `+ prediction_adjustment`.
2. **Whale Alert robustness** — Setelah fix, field API response (dict/string/null) dinormalisasi dengan `_safe_owner()` helper.
3. **Macro calendar JSON** — Sederhana, mudah di-maintain manual. Midnight boundary fix penting (event on current date treated as active until 23:59).

## What Didn't Go Well

1. **Research multiplier dekoratif** — CRITICAL bug: multiplier dihitung tapi tidak pernah diterapkan ke `signal.confidence`. Fungsi `apply_research_to_confidence()` ada tapi tidak pernah dipanggil. Ketahuan di code review.
2. **"All unavailable" detection broken** — `sentiment_score` selalu float 50.0 (bukan None), jadi `all_unavailable` selalu False. Pesan "(Technical confidence only)" tidak pernah tampil.
3. **CoinGecko `total_volumes` sebagai proxy** — CoinGecko free tier tidak memberikan active addresses, jadi pakai trading volume sebagai proxy. Accuracy compromise yang harus didokumentasikan.

## Key Insights

- **Pipeline integration adalah titik lemah konsisten.** Sama seperti Epic 1, bug paling besar adalah wiring: multiplier tidak terkonek ke confidence, flag `research_unavailable` tidak diset.
- **Edge case di API eksternal perlu defensive coding.** Whale Alert bisa return string/dict/null untuk field yang sama. Polymarket probability bisa None. Tanpa guard, satu field aneh crash seluruh stage.
- **Midnight boundary penting untuk calendar events.** Events pada tanggal yang sama dengan pipeline run harus tetap dianggap aktif sampai 23:59 UTC, bukan hilang jam 00:00.

## Action Items

1. [ ] **Setiap kali menambah pipeline stage, verifikasi end-to-end output.** Jangan hanya unit test per module.
2. [ ] **Dokumentasikan data source limitation** — CoinGecko proxy, Reddit tanpa 24h filter, Polymarket staleness.
3. [x] **Whale Alert 3x retry** — diimplementasikan di fix branch.

---

## Epic 3 Retrospective — Self-Improving Accuracy

**Date:** 2026-06-28 | **Stories:** 3/3 done | **Tests:** 51

---

## Summary

Epic 3 menutup loop pembelajaran: outcome resolution → LLM reflection → adaptive weight adjustment via EMA. Pipeline sekarang belajar dari sinyal sebelumnya dan menyesuaikan bobot riset secara otomatis.

---

## What Went Well

1. **EMA closed-loop berfungsi** — `adjust_weights()` menghitung per-source accuracy, update weight via EMA, clamp [0.5,1.5], persist ke SQLite.
2. **Backward compatibility** — `sentiment_mult(score, weight=None)` tetap menggunakan static thresholds saat weight tidak tersedia. Kode existing tidak rusak.
3. **LLM integration robust** — TokenRouter API dipanggil dengan timeout, deterministic fallback pada semua failure mode. Tanpa API key → skip, tidak crash.

## What Didn't Go Well

1. **Accuracy computation action-awareness** — Awalnya `_compute_onchain_accuracy` dan `_compute_prediction_accuracy` tidak memperhitungkan `signal.action` (BUY/SELL). 4 dari 8 kombinasi prediksi salah. Difix di code review.
2. **`_get_dynamic_weights` bare `except: pass`** — Semua DB error (missing table, corruption) di-silence tanpa log. Connection tidak di-close dengan `try/finally`.
3. **Schema drift lagi** — `_clamp_weight` tidak handle NaN, `'unresolvable'` status missing dari CHECK constraint (lagi). Pola yang sama dengan Epic 1.
4. **Prediction weight tidak dikonsumsi** — Weight untuk prediction markets dihitung dan disimpan, tapi tidak dipakai di multiplier formula. Masih pakai `prediction_adjustment` langsung tanpa dikali weight.

## Key Insights

- **Pola berulang:** DB schema drift dan wiring gap di ketiga epic. Ini area improvement paling penting.
- **Backward compatibility dengan default parameter** (`weight=None`) adalah pattern yang bagus untuk feature rollout bertahap.
- **EMA slow adaptation bagus** — α=0.2 berarti sistem tidak overreact ke noise. Butuh ~30 hari untuk konvergen.

## Action Items

1. [ ] **Tambahkan DB migration helper** — function `ensure_column()` atau `ensure_table()` yang idempoten.
2. [ ] **Integration test untuk full pipeline** — test yang menjalankan `run_pipeline()` dengan mock data.
3. [ ] **Prediction weight wiring** — konsumsi `prediction_weight` di `compute_research_multiplier()`.

---

## Overall Lessons (All 3 Epics)

| Pattern | Appeared In | Fix |
|---------|-------------|-----|
| **DB schema drift** (CHECK constraint, missing columns) | Epic 1, 3 | Migration helper |
| **Wiring gap** (stage output tidak terkonek ke input stage berikutnya) | Epic 1, 2 | Integration test |
| **Edge case di API eksternal** (null, string type, NaN) | Epic 2, 3 | Defensive coding |
| **Docs-code mismatch** (architecture vs implementation) | Epic 1 | Sync setelah story |
| **Bare except:pass** (silent failure) | Epic 3 | Logged fallback |

**Total:** 17 stories, 3 code reviews, 51 tests, 1 product shipped 🚀
