import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const client = new QueryClient();
createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={client}><main><h1>Post Chief</h1><p>Social publishing for 404 Builds.</p></main></QueryClientProvider></React.StrictMode>);
