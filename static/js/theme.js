// This script runs before the stylesheets load, so the page never paints in the wrong theme.
document.documentElement.setAttribute('data-bs-theme', localStorage.getItem('theme') || 'dark');
