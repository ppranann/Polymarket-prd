// Supabase Configuration
// Copy this file to config.js and fill in your credentials

const SUPABASE_CONFIG = {
    url: 'YOUR_SUPABASE_URL',  // e.g., 'https://xyz.supabase.co'
    anonKey: 'YOUR_SUPABASE_ANON_KEY'  // Get this from Supabase dashboard
};

// Export for use in dashboard.js
window.SUPABASE_CONFIG = SUPABASE_CONFIG;
