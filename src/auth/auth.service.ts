import { jwtSecret } from './jwt-secret';
import * as bcrypt from 'bcryptjs'
import { HttpException, HttpStatus, Injectable, UnauthorizedException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { InjectModel } from '@nestjs/mongoose';
import { Model } from 'mongoose';
import { User, UserDocument } from '../user/user.schema';

@Injectable()
export class AuthService {
  constructor(
    @InjectModel(User.name) private userModel: Model<UserDocument>,
    private readonly jwtService: JwtService,
  ) { }

  async validateUser(username: string, password: string): Promise<any> {
    const user = await this.userModel.findOne({ username });
    if (user) {
      if (await bcrypt.compare(password, user.password)) {
        const { password, ...result } = user.toObject();
        return result;
      } else {
        throw new HttpException('Usuario o contraseña no válidos', HttpStatus.UNAUTHORIZED)
      }
    } else {
      throw new HttpException('Usuario o contraseña no válidos', HttpStatus.UNAUTHORIZED)
    }
  }

  async login(user: any) {
    const payload = { username: user.username, sub: user._id, profileType: user.profileType, access: user.access };

    const accessToken = this.jwtService.sign({ ...payload, tokenUse: 'access' }, {
      secret: jwtSecret(),
      expiresIn: '1h'
    });

    const refreshToken = this.jwtService.sign({ ...payload, tokenUse: 'refresh' }, {
      secret: jwtSecret(),
      expiresIn: '30d'
    });

    // Guardar refresh token hasheado en DB
    const hashedRt = await bcrypt.hash(refreshToken, 10);
    await this.userModel.findByIdAndUpdate(user._id, { refreshToken: hashedRt });

    return { access_token: accessToken, refresh_token: refreshToken };
  }

  async logout(userId: string) {
    await this.userModel.findByIdAndUpdate(userId, { refreshToken: null})
  }

  async refreshTokens(userId: string, rt: string) {
    const user = await this.userModel.findById(userId);
    if (!user || !user.refreshToken) throw new UnauthorizedException('No autorizado');

    const rtMatches = await bcrypt.compare(rt, user.refreshToken);
    if (!rtMatches) throw new UnauthorizedException('Refresh token inválido');

    const payload = { username: user.username, sub: user._id, profileType: user.profileType, access: user.access };

    const newAccessToken = this.jwtService.sign({ ...payload, tokenUse: 'access' }, {
      secret: jwtSecret(),
      expiresIn: '1h'
    });

    // const newRefreshToken = this.jwtService.sign({ ...payload, tokenUse: 'access' }, {
    //   secret: jwtSecret(),
    //   expiresIn: '30d'
    // });

    // Rotar refresh token
    // const hashedRt = await bcrypt.hash(newRefreshToken, 10);
    // await this.userModel.findByIdAndUpdate(user._id, { refreshToken: hashedRt });

    return { access_token: newAccessToken };
  }


  async register(username: string, password: string, email: string, profileType: string) {
    const hashedPassword = await bcrypt.hash(password, 10);
    const newUser = new this.userModel({ username, password: hashedPassword, email, profileType });
    await newUser.save();
    return { _id: newUser._id, username: newUser.username };
  }
}
