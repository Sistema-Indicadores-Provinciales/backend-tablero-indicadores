import { UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from './auth.guard';
import { AdminGuard } from './admin.guard';
import { jwtSecret } from './jwt-secret';
import {
  Body,
  Controller,
  Post,
  Req,
  Res,
  UnauthorizedException
} from '@nestjs/common';
import { AuthService } from './auth.service';
import { UserDocument } from '../user/user.schema';
import { JwtService } from '@nestjs/jwt';
import { Request, Response } from 'express';

@Controller('auth')
export class AuthController {
  constructor(
    private readonly authService: AuthService,
    private readonly jwtService: JwtService
  ) {}

  // Genera tanto el refresh como el access token, y envía el refresh a la cookie
  @Post('login')
  async login(@Req() req: Request, @Res({ passthrough: true }) res: Response) {
    const user: UserDocument = await this.authService.validateUser(
      req.body.username,
      req.body.password
    );

    const { access_token, refresh_token } = await this.authService.login(user);

    // Guardar refresh token en cookie HttpOnly
    res.cookie('refresh_token', refresh_token, {
      httpOnly: true,        // no accesible por JS
      secure: process.env.NODE_ENV === 'production',         // en local puede ser false, en prod true
      sameSite: 'strict',    // evita CSRF básico
      path: process.env.COOKIE_PATH || '/auth/refresh', // solo se envía a este endpoint
      maxAge: 30 * 24 * 60 * 60 * 1000 // 30 días
    });

    return { access_token };
  }

  // Lee el refresh desde la cookie y devuelve un nuevo access
  @Post('refresh')
  async refresh(@Req() req: Request) {
    const rt = req.cookies['refresh_token'];
    if (!rt) throw new UnauthorizedException('No hay refresh token');

    try {
      const decoded = this.jwtService.verify(rt, { secret: jwtSecret() });
      if (decoded.tokenUse !== 'refresh') throw new UnauthorizedException();
      return this.authService.refreshTokens(decoded.sub, rt);
    } catch {
      throw new UnauthorizedException('Refresh token inválido o expirado');
    }
  }

  // Borra refresh en la BD y limpia la cookie
  @UseGuards(JwtAuthGuard)
  @Post('logout')
  async logout(@Req() req: any, @Res({ passthrough: true }) res: Response) {
    const userId = req.user.sub;
    await this.authService.logout(userId);

    res.clearCookie('refresh_token', { path: process.env.COOKIE_PATH || '/auth/refresh' });
    return { message: 'Sesión cerrada' };
  }

  @UseGuards(AdminGuard)
  @Post('register')
  async register(@Body() body: any) {
    const { username, password, email, profileType } = body;
    return this.authService.register(username, password, email, profileType);
  }
}
