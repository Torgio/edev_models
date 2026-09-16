import { proxyBatteryStudy } from '@/lib/battery-study-proxy';

export const dynamic = 'force-dynamic';

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const development = process.env.NODE_ENV === 'development';
  const dashboardUpstream = development
    ? process.env.DASHBOARD_DEV_API_URL ?? 'http://127.0.0.1:8000'
    : process.env.DASHBOARD_API_URL ?? '';
  return proxyBatteryStudy(request, path.join('/'), {
    upstream: dashboardUpstream,
    studyUpstream: process.env.BESS_STUDY_API_URL,
    enabled: process.env.BESS_STUDY_ENABLED === '1' || development,
    development,
    allowedRuns: process.env.BESS_STUDY_RUN_IDS ?? '',
  });
}

export const GET = handle;
export const POST = handle;
