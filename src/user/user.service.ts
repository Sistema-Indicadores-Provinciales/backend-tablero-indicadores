import * as bcrypt from 'bcryptjs';
import { HttpException, HttpStatus, Injectable } from '@nestjs/common';
import { InjectModel } from '@nestjs/mongoose';
import { User, UserDocument } from './user.schema';
import { Model, Types } from 'mongoose';
import { Dashboard } from '../dashboard/dashboard.schema';

@Injectable()
export class UserService {
  constructor(
    @InjectModel(User.name) private userModel: Model<UserDocument>,
    @InjectModel(Dashboard.name) private readonly dashboardModel: Model<Dashboard>,
  ) { }

  async getAllUsers(): Promise<User[]> {
    return this.userModel.find().exec();
  }

  async createNewUser(username: string, password: string, email: string, profileType: string) {
    const salt = bcrypt.genSaltSync(10);
    const hashedPassword = bcrypt.hashSync(password, salt);
    const newUser = new this.userModel({ username, password: hashedPassword, email, profileType });
    return newUser.save();
  }

  async deleteUser(username: string, email: string): Promise<{ message: string }> {
    const user = await this.userModel.findOneAndDelete({ username, email });
    if (!user) {
      throw new Error('Usuario no encontrado.');
    }
    return { message: 'Usuario eliminado correctamente.' };
  }

  // Este método podría ser utilizado para obtener los dashboards filtrados por el acceso asignado
  async getUserDashboards(userId: string): Promise<Dashboard[]> {
    const user = await this.userModel.findById(userId).populate<{ dashboards: Dashboard[] }>('dashboards');
    // La lógica adicional de filtrado según user.access se aplicaría aquí, dependiendo de tu requerimiento.
    return user?.dashboards || [];
  }

  async updateUserAccess( userId: string, access: { dashboard: string; sections: string[] }[], ): Promise<User> {
    // Convertimos strings a ObjectId
    const newAccess = access.map(a => ({
      dashboard: new Types.ObjectId(a.dashboard),
      sections: a.sections.map(s => new Types.ObjectId(s)),
    }));

    // Actualizamos y retornamos el documento nuevo
    const updated = await this.userModel
      .findByIdAndUpdate(
        userId,
        { access: newAccess },
        { new: true, runValidators: true },
      )
      .exec();

    if (!updated) {
      throw new HttpException('Usuario no encontrado', HttpStatus.NOT_FOUND);
    }

    return updated;
  }

  async getMyDashboards(userId: string): Promise<any[]> {
    const user = await this.userModel.findById(userId).exec();
    if (!user) throw new HttpException('Usuario no encontrado', HttpStatus.NOT_FOUND);

    const result = await Promise.all(
      user.access.map(async (a) => {
        const dashboard = await this.dashboardModel
          .findOne({ _id: a.dashboard, show: true, deletedAt: null })
          .populate('sections', 'keyname name show')
          .exec();

        if (!dashboard) return null;

        const accessibleSections = (dashboard.sections as any[])
          .filter(s => s.show && a.sections.some(id => id.toString() === s._id.toString()));

        if (dashboard.generatedWorkspaceId && !accessibleSections.length) return null;

        return {
          keyname: dashboard.keyname,
          name: dashboard.name,
          icon: dashboard.icon,
          sections: accessibleSections.map(s => s.keyname)
        };
      })
    );

    return result.filter(Boolean);
  }

}
