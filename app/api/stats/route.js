import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return Response.json(await runWorker(["stats"]));
  } catch (error) {
    return jsonError(error);
  }
}
