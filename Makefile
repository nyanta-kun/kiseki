# kiseki - ローカルCIチェック
# プッシュ前に: make check

.PHONY: check check-backend check-frontend

## プッシュ前の全チェック（CIと同等）
check: check-backend check-frontend
	@echo ""
	@echo "✓ 全チェック通過"

## バックエンドチェック（ruff + mypy + pytest）
check-backend:
	@echo "=== Backend: ruff ==="
	cd backend && .venv/bin/ruff check .
	@echo "=== Backend: mypy ==="
	cd backend && .venv/bin/mypy src/ --ignore-missing-imports
	@echo "=== Backend: pytest ==="
	cd backend && .venv/bin/python -m pytest tests/ -q --tb=short

## フロントエンドチェック（tsc + eslint）
check-frontend:
	@echo "=== Frontend: tsc ==="
	cd frontend && pnpm tsc --noEmit
	@echo "=== Frontend: eslint ==="
	cd frontend && pnpm lint
