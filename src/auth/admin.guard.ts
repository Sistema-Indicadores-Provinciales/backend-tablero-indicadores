import { ExecutionContext, ForbiddenException, Injectable } from '@nestjs/common';
import { JwtAuthGuard } from './auth.guard';

@Injectable()
export class AdminGuard extends JwtAuthGuard {
  async canActivate(context: ExecutionContext): Promise<boolean> {
    await super.canActivate(context);
    if (context.switchToHttp().getRequest().user.profileType !== 'ADMIN') {
      throw new ForbiddenException('Se requiere un administrador.');
    }
    return true;
  }
}
