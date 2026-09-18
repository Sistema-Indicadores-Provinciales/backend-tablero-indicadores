import { AdminGuard } from '../auth/admin.guard';
import { Body, Controller, Delete, Get, HttpException, HttpStatus, Param, Post, Put, Request, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from 'src/auth/auth.guard';
import { User, UserDocument } from './user.schema';
import { UserService } from './user.service';

@Controller('user')
export class UserController {
  constructor(private readonly userService: UserService) { }

  @UseGuards(JwtAuthGuard)
  @Get()
  getProfile(@Request() req) {
    const { username, profileType, access } = req.user as UserDocument;
    return { _id: req.user.sub, username, profileType, access };
  }

  @UseGuards(AdminGuard)
  @Post()
  async createNewUser(@Body() body: any) {
    const { username, password, email, profileType } = body;
    return this.userService.createNewUser(username, password, email, profileType);
  }

  @UseGuards(AdminGuard)
  @Delete()
  async deleteUser(@Body() body: any) {
    const { username, email } = body;
    try {
      return await this.userService.deleteUser(username, email);
    } catch (error) {
      throw new HttpException(
        error.message || 'Error al eliminar el usuario',
        HttpStatus.BAD_REQUEST
      );
    }
  }

  @UseGuards(AdminGuard)
  @Get('get-all')
  async getAllUsers(): Promise<User[]> {
    try {
      const users = await this.userService.getAllUsers();
      return users.map((user: any) => {
        const { password, refreshToken, ...safe } = user.toObject ? user.toObject() : user;
        return safe;
      });
    } catch (error) {
      throw new HttpException(
        `Error al obtener los usuarios: ${error.message}`,
        HttpStatus.INTERNAL_SERVER_ERROR
      );
    }
  }

  @UseGuards(AdminGuard)
  @Post('access/:userId')
  async updateAccess(
    @Param('userId') userId: string,
    @Body('access') access: { dashboard: string; sections: string[] }[],
  ) {
    try {
      return await this.userService.updateUserAccess(userId, access);
    } catch (err) {
      throw new HttpException(
        err.message || 'Error al actualizar access',
        err.status || HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }
  }

  @UseGuards(JwtAuthGuard)
  @Get('my-dashboards')
  async getMyDashboards(@Request() req) {
    const userId = req.user.sub.toString();
    return this.userService.getMyDashboards(userId);
  }
}
