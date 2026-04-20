.PHONY: install test diag inspect-diag db-stats inspect-error api-probe

SHELL := /bin/bash

install:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -U pip && pip install -e '.[dev]'

test:
	. .venv/bin/activate && pytest

# make diag [URL=https://...] [ROUNDS=N] [DELAY=N]
diag:
	@flags=""; \
	  if [ -n "$(ROUNDS)" ]; then flags="$$flags --max-scroll-rounds $(ROUNDS)"; fi; \
	  if [ -n "$(DELAY)" ]; then flags="$$flags --wait-delay-seconds $(DELAY)"; fi; \
	  LINKEDCRAWLER_EXTRA_FLAGS="$$flags" bash scripts/diag.sh $(URL)

# make inspect-diag [DIR=output/diag/<stamp>]  (defaults to newest)
inspect-diag:
	@DIR="$(DIR)"; \
	  if [ -z "$$DIR" ]; then DIR=$$(ls -1dt output/diag/*/ 2>/dev/null | head -1); fi; \
	  DIR=$${DIR%/}; \
	  if [ -z "$$DIR" ] || [ ! -d "$$DIR" ]; then echo "no diag dir found"; exit 1; fi; \
	  echo "dir: $$DIR"; echo; \
	  echo "=== per-round post counts ==="; \
	  for f in "$$DIR"/*.html; do \
	    [ -f "$$f" ] || continue; \
	    base=$$(basename "$$f"); \
	    [[ "$$base" == auth-* ]] && continue; \
	    posts=$$(grep -oE 'data-urn="urn:li:activity:[0-9]+"' "$$f" | sort -u | wc -l); \
	    size=$$(wc -c < "$$f"); \
	    printf "  %-30s size=%8d posts=%d\n" "$$base" "$$size" "$$posts"; \
	  done; echo; \
	  echo "=== events.jsonl ==="; [ -f "$$DIR/events.jsonl" ] && cat "$$DIR/events.jsonl" || echo "(none)"; echo; \
	  echo "=== summary.json ==="; [ -f "$$DIR/summary.json" ] && cat "$$DIR/summary.json" || echo "(none)"; echo; \
	  echo "=== stdout.log (tail -15) ==="; [ -f "$$DIR/stdout.log" ] && tail -15 "$$DIR/stdout.log" || echo "(none)"

# make db-stats [DB=data/<name>.sqlite3]  (defaults to newest .sqlite3 under data/)
db-stats:
	@DB="$(DB)"; \
	  if [ -z "$$DB" ]; then DB=$$(ls -t data/*.sqlite3 2>/dev/null | head -1); fi; \
	  if [ -z "$$DB" ] || [ ! -f "$$DB" ]; then echo "no sqlite db found"; exit 1; fi; \
	  echo "db: $$DB"; echo; \
	  echo "=== tables ==="; sqlite3 "$$DB" ".tables"; echo; \
	  echo "=== row counts ==="; \
	  for t in seen_activities exported_notes crawl_runs; do \
	    n=$$(sqlite3 "$$DB" "SELECT COUNT(*) FROM $$t;" 2>/dev/null || echo "n/a"); \
	    printf "  %-20s %s\n" "$$t" "$$n"; \
	  done

# make api-probe [URL=...] [PAGES=N] [DELAY=N] [SAVE_BODIES=1]  (defaults to Simon Wardley, 50 pages)
api-probe:
	@flags=""; \
	  if [ -n "$(PAGES)" ]; then flags="$$flags --max-pages $(PAGES)"; fi; \
	  if [ -n "$(DELAY)" ]; then flags="$$flags --delay-seconds $(DELAY)"; fi; \
	  if [ -n "$(SAVE_BODIES)" ]; then flags="$$flags --save-bodies"; fi; \
	  url="$(URL)"; \
	  if [ -z "$$url" ]; then url="https://www.linkedin.com/in/simonwardley/recent-activity/all/"; fi; \
	  . .venv/bin/activate && python scripts/api_probe.py $$url $$flags

# make inspect-error [DIR=error_logs/<stamp>]  (defaults to newest)
inspect-error:
	@DIR="$(DIR)"; \
	  if [ -z "$$DIR" ]; then DIR=$$(ls -1dt error_logs/*/ 2>/dev/null | head -1); fi; \
	  DIR=$${DIR%/}; \
	  if [ -z "$$DIR" ] || [ ! -d "$$DIR" ]; then echo "no error_logs dir found"; exit 1; fi; \
	  echo "dir: $$DIR"; echo; \
	  if [ -f "$$DIR/error.log" ]; then \
	    echo "=== error.log (tail -20) ==="; tail -20 "$$DIR/error.log"; echo; \
	  fi; \
	  F="$$DIR/page.html"; \
	  if [ -f "$$F" ]; then \
	    echo "=== page.html ==="; \
	    printf "  %-14s %s\n" "title:"       "$$(grep -oE '<title>[^<]*</title>' "$$F" | head -1)"; \
	    printf "  %-14s %s\n" "size:"        "$$(wc -c < "$$F")"; \
	    printf "  %-14s %s\n" "activity urns:" "$$(grep -c 'urn:li:activity:' "$$F")"; \
	    printf "  %-14s %s\n" "auth markers:" "$$(grep -ioE 'captcha|checkpoint|challenge|authwall|sign in|unusual activity' "$$F" | sort -u | paste -sd',' -)"; \
	  fi
