import { jsonError, runWorker } from "@/src/lib/worker";

export async function GET() {
  try {
    return Response.json(await runWorker(["latest-job", "--type", "full_run"]));
  } catch (error) {
    return jsonError(error);
  }
}
