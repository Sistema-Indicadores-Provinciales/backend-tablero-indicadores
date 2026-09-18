import { UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/auth.guard';
import { AdminGuard } from '../auth/admin.guard';
import { Controller, Get, Post, Body, Param, HttpException, HttpStatus, Delete, Put } from '@nestjs/common';
import { SectionsService } from './sections.service';
import { DashboardLifecycleService } from '../dashboard/dashboard-lifecycle.service';

@UseGuards(JwtAuthGuard)
@Controller('sections')
export class SectionsController {
  constructor(private readonly sectionsService: SectionsService, private readonly lifecycle: DashboardLifecycleService) {}

  @UseGuards(AdminGuard)
  @Post('add')
  async createSection(@Body() body: any) {
    const { keyname, name, show } = body;
    return await this.sectionsService.createSection(keyname, name, show);
  }

  @Get('get-all')
  async getAllSections() {
    return await this.sectionsService.getAllSections();
  }

  @UseGuards(AdminGuard)
  @Put('edit/:sectionId')
  async editSection(
    @Param('sectionId') sectionId: string,
    @Body() body: any
  ) {
    const { newKeyname, newName, show } = body;
    if (!sectionId) {
      throw new HttpException(
        'El id de la sección es obligatorio.',
        HttpStatus.BAD_REQUEST
      );
    }
    return this.sectionsService.editSection(sectionId, newKeyname, newName, show);
  }

  @UseGuards(AdminGuard)
  @Delete('delete/:sectionId')
  async deleteSection(@Param('sectionId') sectionId: string) {
    try {
      return await this.lifecycle.deleteSection(sectionId);
    } catch (error) {
      throw new HttpException(error.message || 'Error eliminando la sección', error.status || HttpStatus.INTERNAL_SERVER_ERROR);
    }
  }
}
