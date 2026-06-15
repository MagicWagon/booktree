import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request) {
  try {
    const body = await request.json().catch(() => ({}));
    const args = ["import-logs"];
    if (body.logFile) {
      args.push("--log-file", body.logFile);
    }
    return Response.json(await runWorker(args));
  } catch (error) {
    return jsonError(error);
  }
}
