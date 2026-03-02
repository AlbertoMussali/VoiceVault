.PHONY: dev-up dev-down lint test

dev-up:
	@if [ -f docker-compose.yml ] || [ -f compose.yml ] || [ -f compose.yaml ]; then \
		docker compose up -d; \
	else \
		echo "No Docker Compose file found; skipping dev-up."; \
	fi

dev-down:
	@if [ -f docker-compose.yml ] || [ -f compose.yml ] || [ -f compose.yaml ]; then \
		docker compose down; \
	else \
		echo "No Docker Compose file found; skipping dev-down."; \
	fi

lint:
	@echo "No lint configuration found; lint target is a no-op."

test:
	@if [ -f todo.ts ]; then \
		npx ts-node todo.ts list; \
	else \
		echo "No todo.ts found; test target is a no-op."; \
	fi
