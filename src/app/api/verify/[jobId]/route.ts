import { NextRequest, NextResponse } from 'next/server';
import { handleVerifyPoll } from '../../../../lib/verify-route';

interface RouteContext {
  params: Promise<{
    jobId: string;
  }>;
}

export async function GET(_request: NextRequest, context: RouteContext) {
  const { jobId } = await context.params;
  const response = await handleVerifyPoll(jobId);
  return NextResponse.json(response.body, { status: response.status });
}
