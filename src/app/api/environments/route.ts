import { NextResponse } from 'next/server';

import type { EnvironmentsResponse } from '@/types/verification';

const KIMINA_LEAN_SERVER_URL = process.env.KIMINA_SERVER_URL || 'http://localhost:10000';

const FALLBACK_ENVIRONMENTS: EnvironmentsResponse = {
  default_environment: 'mathlib-v4.15',
  environments: [
    {
      id: 'mathlib-v4.15',
      display_name: 'Mathlib 4.15',
      lean_version: 'v4.15.0',
      project_label: 'Mathlib',
      project_type: 'mathlib',
      selectable: true,
      auto_routable: true,
      is_default: true,
    },
    {
      id: 'mathlib-v4.27',
      display_name: 'Mathlib 4.27',
      lean_version: 'v4.27.0',
      project_label: 'Mathlib',
      project_type: 'mathlib',
      selectable: true,
      auto_routable: true,
      is_default: false,
    },
    {
      id: 'formal-conjectures-v4.27',
      display_name: 'Formal Conjectures 4.27',
      lean_version: 'v4.27.0',
      project_label: 'FormalConjectures',
      project_type: 'formal-conjectures',
      selectable: true,
      auto_routable: true,
      is_default: false,
    },
  ],
};

export async function GET() {
  try {
    const response = await fetch(`${KIMINA_LEAN_SERVER_URL}/api/environments`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      next: { revalidate: 0 },
      signal: AbortSignal.timeout(10000),
    });

    if (!response.ok) {
      return NextResponse.json(FALLBACK_ENVIRONMENTS);
    }

    const environments = (await response.json()) as EnvironmentsResponse;
    return NextResponse.json(environments);
  } catch {
    return NextResponse.json(FALLBACK_ENVIRONMENTS);
  }
}
