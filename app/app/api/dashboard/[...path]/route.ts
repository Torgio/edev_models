import { proxyDashboardRequest } from '@/lib/dashboard-proxy';

export const dynamic = 'force-dynamic';

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const development = process.env.NODE_ENV === 'development';
  const upstream = development
    ? process.env.DASHBOARD_DEV_API_URL ?? 'http://127.0.0.1:8000'
    : process.env.DASHBOARD_API_URL ?? 'https://91.134.143.153';
  return proxyDashboardRequest(request, path.join('/'), { upstream, development });
}

export { handle as GET, handle as POST };
