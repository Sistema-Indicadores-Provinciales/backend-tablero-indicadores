import { Injectable } from '@nestjs/common';
import { InjectModel } from '@nestjs/mongoose';
import { Model } from 'mongoose';
import { Section, SectionDocument } from './sections.schema';

@Injectable()
export class SectionsService {
  constructor(
    @InjectModel(Section.name) private sectionModel: Model<SectionDocument>,
  ) {}

  async createSection(keyname: string, name: string, show: boolean) {
    try {
      const newSection = new this.sectionModel({ keyname, name, show });
      return await newSection.save();
    } catch (error) {
      console.error('Error al crear la sección: ', error);
      throw error;
    }
  }

  async getAllSections(): Promise<SectionDocument[]> {
    return await this.sectionModel.find({ deletedAt: null }).exec();
  }

  async editSection(sectionId: string, newKeyname?: string, newName?: string, show?: boolean) {
    try {
      const updateData: Partial<{ keyname: string; name: string; show: boolean }> = {};

      if (newKeyname) updateData.keyname = newKeyname;
      if (newName) updateData.name = newName;
      if (typeof show === 'boolean') updateData.show = show;

      if (Object.keys(updateData).length > 0) {
        const editedSection = await this.sectionModel.findOneAndUpdate(
          { _id: sectionId, deletedAt: null },
          { $set: updateData },
          { new: true }
        );
        return editedSection;
      } else {
        throw new Error('No hay campos para actualizar');
      }
    } catch (error) {
      console.error('Error al editar la sección: ', error);
      throw error;
    }
  }

}
