#!/bin/bash
# Start all FarmTwin Docker services

set -e

echo "================================================"
echo "Starting FarmTwin Docker Services"
echo "================================================"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "\n${YELLOW}Step 1: Starting PostgreSQL database...${NC}"
docker compose up -d db
echo "Waiting for database to be healthy..."
timeout 60 bash -c 'until docker compose exec db pg_isready -U farmtwin_admin -d farmtwin; do sleep 2; done' || {
    echo -e "${RED}Database failed to become ready${NC}"
    exit 1
}
echo -e "${GREEN}✓ Database is ready${NC}"

echo -e "\n${YELLOW}Step 2: Starting Redis...${NC}"
docker compose up -d redis
echo "Waiting for Redis to be healthy..."
timeout 30 bash -c 'until docker compose exec redis redis-cli ping | grep -q PONG; do sleep 2; done' || {
    echo -e "${RED}Redis failed to become ready${NC}"
    exit 1
}
echo -e "${GREEN}✓ Redis is ready${NC}"

echo -e "\n${YELLOW}Step 3: Checking migrations...${NC}"
# Check if migrations need to be run
MIGRATION_STATUS=$(docker compose exec db psql -U farmtwin_admin -d farmtwin -tAc "SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name='alembic_version');" 2>/dev/null || echo "f")

if [ "$MIGRATION_STATUS" = "f" ]; then
    echo "Database schema not initialized. Running migrations from backend..."
    echo -e "${YELLOW}Please run migrations manually:${NC}"
    echo "  cd backend"
    echo "  source venv/bin/activate"
    echo "  python -m app.db.management upgrade"
    echo "  python -m app.db.demo_setup"
else
    echo -e "${GREEN}✓ Database schema is initialized${NC}"
fi

echo -e "\n${YELLOW}Step 4: Starting API service...${NC}"
docker compose up -d api
echo "Waiting for API to be healthy..."
sleep 10
timeout 60 bash -c 'until curl -sf http://localhost:8000/health > /dev/null; do sleep 2; done' || {
    echo -e "${RED}API failed to become ready${NC}"
    echo "Checking API logs:"
    docker compose logs api | tail -20
    exit 1
}
echo -e "${GREEN}✓ API is ready${NC}"

echo -e "\n${YELLOW}Step 5: Starting worker service...${NC}"
docker compose up -d farmtwin-worker
echo -e "${GREEN}✓ Worker started${NC}"

echo -e "\n${GREEN}================================================${NC}"
echo -e "${GREEN}All services started successfully!${NC}"
echo -e "${GREEN}================================================${NC}"
echo ""
echo "Service URLs:"
echo "  API:          http://localhost:8000"
echo "  API Docs:     http://localhost:8000/docs"
echo "  Database:     localhost:5432"
echo "  Redis:        localhost:6379"
echo ""
echo "Useful commands:"
echo "  View logs:    docker compose logs -f"
echo "  Stop all:     docker compose down"
echo "  Restart:      docker compose restart"
echo ""
