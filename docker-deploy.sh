#!/bin/bash

# MTFWBuilder Docker Deployment Script
# Usage: ./docker-deploy.sh [command]

set -e

CONTAINER_NAME="mtfwbuilder"
IMAGE_NAME="mtfwbuilder:latest"

show_help() {
    echo "MTFWBuilder Docker Deployment Script"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  build       Build the Docker image"
    echo "  start       Start the container with Docker Compose"
    echo "  stop        Stop the container"
    echo "  restart     Restart the container"
    echo "  logs        Show container logs"
    echo "  shell       Open a shell in the running container"
    echo "  clean       Remove container and images"
    echo "  cleanup     Clean up old build files (admin)"
    echo "  status      Show container status"
    echo "  update      Pull latest changes and rebuild"
    echo "  help        Show this help message"
    echo ""
}

build_image() {
    echo "🔨 Building MTFWBuilder Docker image..."
    docker compose build --no-cache
    echo "✅ Build complete!"
}

start_container() {
    echo "🚀 Starting MTFWBuilder..."
    docker compose up -d
    echo "✅ MTFWBuilder is running at http://localhost:5000"
    echo "📊 Use '$0 logs' to view logs"
    echo "📊 Use '$0 status' to check status"
}

stop_container() {
    echo "🛑 Stopping MTFWBuilder..."
    docker compose down
    echo "✅ MTFWBuilder stopped"
}

restart_container() {
    echo "🔄 Restarting MTFWBuilder..."
    docker compose restart
    echo "✅ MTFWBuilder restarted"
}

show_logs() {
    echo "📋 MTFWBuilder logs (Ctrl+C to exit):"
    docker compose logs -f
}

open_shell() {
    echo "🐚 Opening shell in MTFWBuilder container..."
    docker compose exec mtfwbuilder /bin/bash
}

clean_all() {
    echo "🧹 Cleaning up Docker resources..."
    docker compose down -v --rmi all --remove-orphans
    echo "✅ Cleanup complete"
}

cleanup_builds() {
    echo "🧹 Cleaning up old build files..."
    
    # Try to read admin password from config.json, fallback to default
    if [ -f "config.json" ]; then
        ADMIN_PASS=$(python3 -c "import json; print(json.load(open('config.json')).get('admin_password', 'meshtastic'))" 2>/dev/null || echo "meshtastic")
    else
        ADMIN_PASS="meshtastic"
    fi
    
    curl -X POST -d "admin_key=$ADMIN_PASS" http://localhost:5000/cleanup
    echo "✅ Build cleanup requested"
}

show_status() {
    echo "📊 MTFWBuilder Status:"
    docker compose ps
    echo ""
    echo "📊 Docker Images:"
    docker images | grep -E "(REPOSITORY|mtfwbuilder)"
    echo ""
    echo "📊 Volume Usage:"
    docker volume ls | grep -E "(DRIVER|mtfwbuilder)"
}

update_and_rebuild() {
    echo "🔄 Updating MTFWBuilder..."
    echo "⬇️  Pulling latest changes..."
    git pull
    echo "🛑 Stopping current container..."
    docker compose down
    echo "🔨 Rebuilding image..."
    docker compose build --no-cache
    echo "🚀 Starting updated container..."
    docker compose up -d
    echo "✅ Update complete! MTFWBuilder is running at http://localhost:5000"
}

# Main command handling
case "${1:-help}" in
    build)
        build_image
        ;;
    start)
        start_container
        ;;
    stop)
        stop_container
        ;;
    restart)
        restart_container
        ;;
    logs)
        show_logs
        ;;
    shell)
        open_shell
        ;;
    clean)
        clean_all
        ;;
    cleanup)
        cleanup_builds
        ;;
    status)
        show_status
        ;;
    update)
        update_and_rebuild
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "❌ Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac 