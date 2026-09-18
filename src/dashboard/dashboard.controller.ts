import { Delete, UseGuards } from '@nestjs/common';
import { DashboardLifecycleService } from './dashboard-lifecycle.service';
import { JwtAuthGuard } from '../auth/auth.guard';
import { AdminGuard } from '../auth/admin.guard';
import { Body, Controller, Get, HttpException, HttpStatus, Param, Post, Put } from '@nestjs/common';
import { DashboardService } from './dashboard.service';
import { Dashboard } from './dashboard.schema';

@UseGuards(JwtAuthGuard)
@Controller('dashboard')
export class DashboardController {
  constructor(private readonly dashboardService: DashboardService, private readonly lifecycle: DashboardLifecycleService) {}

  @UseGuards(AdminGuard)
  @Delete(':dashboardId')
  deleteDashboard(@Param('dashboardId') id: string) { return this.lifecycle.deleteDashboard(id); }

  @UseGuards(AdminGuard)
  @Post('reconcile-generated')
  reconcileGenerated() { return this.lifecycle.reconcileGenerated(); }

  @UseGuards(AdminGuard)
  @Post()
  async createNewDashboard(@Body() body: any) {
    const { keyname, name, show, icon } = body;
    return this.dashboardService.createNewDashboard(keyname, name, show, icon);
  }

  @Get('get-all')
  async getAllDashboards(): Promise<Dashboard[]> {
    try {
      const dashboards = await this.dashboardService.getAllDashboards();
      return dashboards;
    } catch (error) {
      throw new HttpException(
        'Error al obtener los tableros',
        HttpStatus.INTERNAL_SERVER_ERROR
      );
    }
  }

  @UseGuards(AdminGuard)
  @Put('edit/:dashboardId')
  async editDashboard(
    @Param('dashboardId') dashboardId: string,
    @Body() body: any
  ) {
    const { newKeyname, newName, show, icon } = body;
    if (!dashboardId) {
      throw new HttpException(
        'El id del dashboard es obligatorio.',
        HttpStatus.BAD_REQUEST
      );
    }
    return this.dashboardService.editDashboard(dashboardId, newKeyname, newName, show, icon);
  }

  @UseGuards(AdminGuard)
  @Post('add/:dashboardId')
  async addSection(
    @Param('dashboardId') dashboardId: string,
    @Body() body: any
  ) {
    const { sections } = body;
    if (!dashboardId) {
      throw new HttpException(
        'El id del dashboard es obligatorio.',
        HttpStatus.BAD_REQUEST
      );
    }
    return this.dashboardService.updateDashboardSections(dashboardId, sections);
  }
}
