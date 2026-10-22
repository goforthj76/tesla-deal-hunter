import {scan} from '../../../lib/hunter';
import {scanDallas} from '../../../lib/dallas';
export async function POST(request:Request){const origin=request.headers.get('origin');if(origin&&origin!==new URL(request.url).origin)return Response.json({error:'Invalid origin'},{status:403});return Response.json({sources:await Promise.allSettled([scan(),scanDallas()])})}
