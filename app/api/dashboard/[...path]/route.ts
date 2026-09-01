import { proxyDashboardRequest } from '@/lib/dashboard-proxy';

export const dynamic = 'force-dynamic';

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const development = process.env.NODE_ENV === 'development';
  const upstream = process.env.DASHBOARD_API_URL
    ?? (development ? process.env.NEXT_PUBLIC_DASHBOARD_API_URL : undefined)
    ?? 'https://91.134.143.153';
  return proxyDashboardRequest(request, path.join('/'), { upstream, development });
}

export { handle as GET, handle as POST };
