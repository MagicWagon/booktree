import { jsonError, runWorker } from "@/src/lib/worker";

export async function POST() {
  try {
    return Response.json(await runWorker(["start-run"]));
  } catch (error) {
    return jsonError(error);
  }
}
