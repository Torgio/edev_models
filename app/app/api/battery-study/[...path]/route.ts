import { proxyBatteryStudy } from '@/lib/battery-study-proxy';

export const dynamic = 'force-dynamic';

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  return proxyBatteryStudy(request, path.join('/'), {
    upstream: process.env.DASHBOARD_API_URL ?? '',
    studyUpstream: process.env.BESS_STUDY_API_URL,
    development: process.env.NODE_ENV === 'development',
    enabled: process.env.BESS_STUDY_ENABLED === '1',
    allowedRuns: process.env.BESS_STUDY_RUN_IDS ?? '',
  });
}

export const GET = handle;
export const POST = handle;
